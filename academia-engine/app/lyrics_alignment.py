"""Provider-neutral timestamped lyrics normalization, mapping, validation and persistence."""
import hashlib,json,os,re,unicodedata
from datetime import datetime,timezone
from enum import Enum
from pathlib import Path
from difflib import SequenceMatcher

from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator


MAX_TIMESTAMP_AUDIO_OVERRUN_SECONDS=0.35
MAX_UNMATCHED_WORD_RATIO=0.25
MAX_UNMATCHED_LINE_RATIO=0.30
MIN_MAPPED_LINE_COVERAGE=0.70
MIN_MONOTONIC_TIMESTAMP_RATIO=0.98


class LyricsAlignmentError(RuntimeError): failure_category="lyrics_alignment_invalid"
class TimestampedLyricsRequestFailed(LyricsAlignmentError): failure_category="timestamped_lyrics_request_failed"
class TimestampedLyricsParseFailed(LyricsAlignmentError): failure_category="timestamped_lyrics_parse_failed"
class LyricsAlignmentMissing(LyricsAlignmentError): failure_category="lyrics_alignment_missing"
class LyricsAlignmentInvalid(LyricsAlignmentError): failure_category="lyrics_alignment_invalid"
class LyricsAlignmentReviewRequired(LyricsAlignmentError): failure_category="lyrics_alignment_review_required"
class LyricsMappingFailed(LyricsAlignmentError): failure_category="lyrics_mapping_failed"
class LyricsSectionTimingFailed(LyricsAlignmentError): failure_category="lyrics_section_timing_failed"


class AlignmentGranularity(str,Enum): WORD="word"; LINE="line"; SECTION="section"
class AlignmentStatus(str,Enum):
    VALID="valid"; VALID_WITH_WARNINGS="valid_with_warnings"; REVIEW_REQUIRED="review_required"
    INVALID="invalid"; INSTRUMENTAL="instrumental"


def normalize_lexical(value):
    value=unicodedata.normalize("NFC",value).casefold().strip()
    value=re.sub(r"\[[^\]]+\]"," ",value)
    return " ".join(re.findall(r"[^\W_]+",value,flags=re.UNICODE))


def _comparison_token(value):
    value=normalize_lexical(value).replace(" ","")
    # Sung vowels are often elongated by the provider transcription.
    return re.sub(r"([aeiouăâî])\1{2,}",r"\1",value)


class AlignedLyricsWord(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    word_id:str; text:str; normalized_text:str
    start_seconds:float=Field(ge=0); end_seconds:float=Field(ge=0)
    source_line_id:str|None=None; confidence:float|None=Field(default=None,ge=0,le=1)
    @model_validator(mode="after")
    def interval(self):
        if self.end_seconds<self.start_seconds: raise ValueError("Aligned word ends before it starts.")
        if not self.normalized_text: raise ValueError("Aligned word has no lexical content.")
        return self


class AlignedLyricsLine(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    line_id:str; source_lyrics_line_id:str|None=None; text:str; normalized_text:str
    start_seconds:float=Field(ge=0); end_seconds:float=Field(ge=0)
    word_ids:tuple[str,...]; section_type:str|None=None
    @model_validator(mode="after")
    def interval(self):
        if self.end_seconds<self.start_seconds: raise ValueError("Aligned line ends before it starts.")
        return self


class LyricsSectionTiming(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    section_id:str; section_type:str; start_seconds:float=Field(ge=0); end_seconds:float=Field(gt=0)
    line_ids:tuple[str,...]=()


class LyricsAlignment(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,allow_inf_nan=False)
    alignment_id:str; variant_id:str; audio_artifact_id:str; audio_sha256:str=Field(pattern=r"^[a-f0-9]{64}$")
    provider_task_id:str; provider_audio_id:str; audio_duration_seconds:float=Field(gt=0)
    language:str; source:str; granularity:AlignmentGranularity
    lines:tuple[AlignedLyricsLine,...]=(); words:tuple[AlignedLyricsWord,...]=()
    sections:tuple[LyricsSectionTiming,...]=(); confidence:float|None=Field(default=None,ge=0,le=1)
    mapping_confidence:float|None=Field(default=None,ge=0,le=1)
    unmatched_provider_tokens:tuple[str,...]=(); unmatched_lyrics_tokens:tuple[str,...]=()
    status:AlignmentStatus; retrieval_status:str; created_at:datetime
    @field_validator("created_at")
    @classmethod
    def aware(cls,value):
        if value.tzinfo is None or value.utcoffset() is None: raise ValueError("Alignment timestamp must be aware.")
        return value


class ProviderAlignedWord(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True,strict=True,allow_inf_nan=False)
    text:str=Field(min_length=1); start_seconds:float; end_seconds:float; confidence:float|None=None


class AlignmentQualityPolicy(BaseModel):
    model_config=ConfigDict(extra="forbid",frozen=True)
    maximum_overrun_seconds:float=MAX_TIMESTAMP_AUDIO_OVERRUN_SECONDS
    maximum_unmatched_word_ratio:float=MAX_UNMATCHED_WORD_RATIO
    maximum_unmatched_line_ratio:float=MAX_UNMATCHED_LINE_RATIO
    minimum_mapped_line_coverage:float=MIN_MAPPED_LINE_COVERAGE
    minimum_monotonic_ratio:float=MIN_MONOTONIC_TIMESTAMP_RATIO


class LyricsAlignmentNormalizer:
    def __init__(self,policy=None): self.policy=policy or AlignmentQualityPolicy()
    def build(self,*,variant_id,audio_artifact_id,audio_sha256,provider_task_id,provider_audio_id,
              audio_duration_seconds,language,source,provider_words,lyrics,instrumental=False):
        if instrumental and not provider_words:
            return LyricsAlignment(alignment_id=f"alignment-{variant_id}",variant_id=variant_id,
                audio_artifact_id=audio_artifact_id,audio_sha256=audio_sha256,provider_task_id=provider_task_id,
                provider_audio_id=provider_audio_id,audio_duration_seconds=audio_duration_seconds,language=language,
                source=source,granularity="word",status="instrumental",retrieval_status="instrumental",
                created_at=datetime.now(timezone.utc))
        if not provider_words: raise TimestampedLyricsParseFailed("Timestamped lyrics contain no aligned words.")
        ordered=list(provider_words)
        if any(ordered[i].start_seconds>ordered[i+1].start_seconds for i in range(len(ordered)-1)):
            ordered.sort(key=lambda value:(value.start_seconds,value.end_seconds))
        words=[]
        for index,value in enumerate(ordered,1):
            normalized=normalize_lexical(value.text)
            if not normalized: continue # section labels/punctuation are structural, not lexical words
            if value.start_seconds<0 or value.end_seconds<value.start_seconds:
                raise LyricsAlignmentInvalid("Provider word timestamp is invalid.")
            if value.end_seconds>audio_duration_seconds+self.policy.maximum_overrun_seconds:
                raise LyricsAlignmentInvalid("Provider timestamp exceeds probed audio duration.")
            words.append(AlignedLyricsWord(word_id=f"{variant_id}-word-{len(words)+1:04d}",text=value.text,
                normalized_text=normalized,start_seconds=value.start_seconds,end_seconds=value.end_seconds,
                confidence=value.confidence))
        if not words: raise TimestampedLyricsParseFailed("Timestamped lyrics contain no lexical words.")
        return self._map(variant_id,audio_artifact_id,audio_sha256,provider_task_id,provider_audio_id,
            audio_duration_seconds,language,source,tuple(words),lyrics)

    def _map(self,variant_id,artifact_id,sha,task_id,audio_id,duration,language,source,words,lyrics):
        targets=[]; line_meta=[]
        for section in lyrics.sections:
            for line in section.lines:
                tokens=normalize_lexical(line.text).split(); start=len(targets)
                targets.extend((token,line.line_id,section.section_id,section.kind.value) for token in tokens)
                line_meta.append((line,section,start,len(targets)))
        provider=[_comparison_token(word.normalized_text) for word in words]
        expected=[_comparison_token(value[0]) for value in targets]
        matcher=SequenceMatcher(None,expected,provider,autojunk=False); mapping={}; matched_provider=set(); matched_expected=set()
        for block in matcher.get_matching_blocks():
            for offset in range(block.size): mapping[block.a+offset]=block.b+offset; matched_expected.add(block.a+offset); matched_provider.add(block.b+offset)
        # Recover small spelling and elongated-syllable variations without crossing sequence order.
        last=-1
        for target_index,token in enumerate(expected):
            if target_index in mapping: last=mapping[target_index]; continue
            candidates=range(last+1,min(len(provider),last+5))
            best=max(candidates,key=lambda i:SequenceMatcher(None,token,provider[i]).ratio(),default=-1)
            if best>=0 and best not in matched_provider and SequenceMatcher(None,token,provider[best]).ratio()>=.72:
                mapping[target_index]=best; matched_expected.add(target_index); matched_provider.add(best); last=best
        mapped_words=list(words); lines=[]; mapped_lines=0
        for line,section,start,end in line_meta:
            indices=[mapping[i] for i in range(start,end) if i in mapping]
            if not indices: continue
            indices=sorted(set(indices)); ids=[]
            for index in indices:
                value=mapped_words[index]; mapped_words[index]=value.model_copy(update={"source_line_id":line.line_id}); ids.append(value.word_id)
            lines.append(AlignedLyricsLine(line_id=f"{variant_id}-line-{len(lines)+1:04d}",source_lyrics_line_id=line.line_id,
                text=line.text,normalized_text=normalize_lexical(line.text),start_seconds=mapped_words[indices[0]].start_seconds,
                end_seconds=mapped_words[indices[-1]].end_seconds,word_ids=tuple(ids),section_type=section.kind.value)); mapped_lines+=1
        total_lines=len(line_meta); line_coverage=mapped_lines/total_lines if total_lines else 0
        unmatched_words=tuple(words[i].text for i in range(len(words)) if i not in matched_provider)
        unmatched_lyrics=tuple(targets[i][0] for i in range(len(targets)) if i not in matched_expected)
        unmatched_word_ratio=len(unmatched_words)/len(words)
        monotonic=sum(a.start_seconds<=b.start_seconds for a,b in zip(words,words[1:]))/max(1,len(words)-1)
        if line_coverage<self.policy.minimum_mapped_line_coverage or 1-line_coverage>self.policy.maximum_unmatched_line_ratio:
            status=AlignmentStatus.REVIEW_REQUIRED
        elif unmatched_word_ratio>self.policy.maximum_unmatched_word_ratio or monotonic<self.policy.minimum_monotonic_ratio:
            status=AlignmentStatus.VALID_WITH_WARNINGS
        else: status=AlignmentStatus.VALID
        sections=[]
        for section in lyrics.sections:
            section_lines=[line for line in lines if line.section_type==section.kind.value and line.source_lyrics_line_id in {x.line_id for x in section.lines}]
            if section_lines: sections.append(LyricsSectionTiming(section_id=section.section_id,section_type=section.kind.value,
                start_seconds=min(x.start_seconds for x in section_lines),end_seconds=max(x.end_seconds for x in section_lines),
                line_ids=tuple(x.line_id for x in section_lines)))
        lyrical=sorted(sections,key=lambda value:value.start_seconds); gaps=[]
        if lyrical and lyrical[0].start_seconds>0:
            gaps.append(LyricsSectionTiming(section_id=f"{variant_id}-instrumental-intro",section_type="instrumental_intro",
                start_seconds=0,end_seconds=lyrical[0].start_seconds))
        for index,(previous,current) in enumerate(zip(lyrical,lyrical[1:]),1):
            if current.start_seconds>previous.end_seconds:
                gaps.append(LyricsSectionTiming(section_id=f"{variant_id}-instrumental-break-{index:02d}",
                    section_type="instrumental_break",start_seconds=previous.end_seconds,end_seconds=current.start_seconds))
        if lyrical and lyrical[-1].end_seconds<duration:
            gaps.append(LyricsSectionTiming(section_id=f"{variant_id}-instrumental-outro",section_type="instrumental_outro",
                start_seconds=lyrical[-1].end_seconds,end_seconds=duration))
        sections=sorted((*sections,*gaps),key=lambda value:value.start_seconds)
        return LyricsAlignment(alignment_id=f"alignment-{variant_id}",variant_id=variant_id,audio_artifact_id=artifact_id,
            audio_sha256=sha,provider_task_id=task_id,provider_audio_id=audio_id,audio_duration_seconds=duration,
            language=language,source=source,granularity="word",lines=tuple(lines),words=tuple(mapped_words),sections=tuple(sections),
            mapping_confidence=len(matched_expected)/max(1,len(expected)),unmatched_provider_tokens=unmatched_words,
            unmatched_lyrics_tokens=unmatched_lyrics,status=status,retrieval_status="retrieved",created_at=datetime.now(timezone.utc))


class LyricsAlignmentStore:
    def __init__(self,directory): self.directory=Path(directory)
    def path(self,variant_id): return self.directory/f"alignment-{variant_id}.json"
    def load_valid(self,variant_id,audio_sha256):
        path=self.path(variant_id)
        if not path.is_file(): return None
        value=LyricsAlignment.model_validate_json(path.read_text(encoding="utf-8"))
        return value if value.audio_sha256==audio_sha256 and value.status in (AlignmentStatus.VALID,AlignmentStatus.VALID_WITH_WARNINGS,AlignmentStatus.INSTRUMENTAL) else None
    def save(self,value):
        path=self.path(value.variant_id); path.parent.mkdir(parents=True,exist_ok=True); part=path.with_suffix(".json.part")
        try:
            part.write_text(value.model_dump_json(indent=2),encoding="utf-8")
            with part.open("r+b") as stream: os.fsync(stream.fileno())
            os.replace(part,path)
        finally: part.unlink(missing_ok=True)


def build_aligned_music_timeline(storyboard,alignment):
    """Project measured lyric onsets onto storyboard order while retaining complete audio coverage."""
    from app.music_timeline import MusicTimeline,MusicTimelineSegment
    sections=storyboard.sections; lines=alignment.lines
    if not sections: raise LyricsSectionTimingFailed("Storyboard contains no sections.")
    boundaries=[0.0]
    for index in range(1,len(sections)):
        target=round(index*len(lines)/len(sections))
        boundary=lines[min(target,len(lines)-1)].start_seconds if lines else index*alignment.audio_duration_seconds/len(sections)
        boundaries.append(max(boundaries[-1],min(boundary,alignment.audio_duration_seconds)))
    boundaries.append(alignment.audio_duration_seconds)
    # Avoid zero-length segments caused by simultaneous provider timestamps.
    if any(b<=a for a,b in zip(boundaries,boundaries[1:])):
        boundaries=[index*alignment.audio_duration_seconds/len(sections) for index in range(len(sections)+1)]
    return MusicTimeline(timeline_id=f"{storyboard.storyboard_id}-{alignment.variant_id}",storyboard_id=storyboard.storyboard_id,
        music_duration_seconds=alignment.audio_duration_seconds,
        segments=tuple(MusicTimelineSegment(start_seconds=boundaries[index],end_seconds=boundaries[index+1],
            storyboard_section_id=section.section_id,estimated_confidence=alignment.mapping_confidence or 0)
            for index,section in enumerate(sections)))
