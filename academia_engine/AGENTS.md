# Reguli de dezvoltare — Academia Engine

## Arhitectură

- Păstrează codul modular, organizat pe responsabilități clare.
- Fiecare engine are o singură responsabilitate.
- Engine-urile comunică exclusiv prin fișiere JSON.
- Niciun engine nu trebuie să cunoască implementarea internă a altui engine.
- Orice model AI sau furnizor AI trebuie să poată fi înlocuit fără modificarea logicii aplicației.

## Cod și modele de date

- Folosește type hints pentru toate interfețele publice, argumentele și valorile returnate.
- Definește modelele pentru fișierele JSON folosind `dataclasses` sau Pydantic.
- Păstrează modelele JSON independente de implementarea engine-urilor.

## Testare

- Fiecare modul trebuie să poată fi testat separat.
- Testele nu trebuie să depindă de API-uri externe, modele AI sau alte engine-uri.
