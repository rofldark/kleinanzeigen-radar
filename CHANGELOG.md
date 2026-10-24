# Changelog

Alle wichtigen Änderungen an diesem Projekt werden hier dokumentiert.
Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.1.0/), Versionen folgen [Semantic Versioning](https://semver.org/lang/de/).

## [Unreleased]

## [1.0.0] – 2026-10-25
### Hinzugefügt
- Erste öffentliche Version von Kleinanzeigen-Radar
- Überwachung beliebig vieler Suchbegriffe und Kleinanzeigen-URLs mit eigenen Filtern je Suche: Ort und Umkreis, Preisbereich, Anzeigen ohne Preis, Pflicht- und Ausschlusswörter, Telegram an/aus
- Erster Durchlauf einer Suche liest nur den Bestand ein, gemeldet werden danach nur neue Anzeigen; ältere Anzeigen gelten nie als neu
- Weboberfläche im Stil eines Mailprogramms (Ordnerleiste, Trefferliste, Vorschau mit Preisverlauf), smartphone-tauglich, Hell- und Dunkelmodus
- Telegram-Benachrichtigung mit Foto, Titel, Preis, Ort und Link (optional, Zugangsdaten per Umgebungsvariable bzw. `.env`)
- Zufälliges Abrufintervall, globale Blacklist, „Gesuch“-Filter, Such-Sets
- Einstellung „Orte hervorheben“: Postleitzahlen mit bestimmten Anfängen werden farbig markiert
- Parser-Wächter mit Warnung, wenn Suchen plötzlich nichts mehr finden oder dauerhaft fehlschlagen
- Dauerhafter Suchverlauf in SQLite (`data/history.db`) mit Ansicht „Datenbank“ und Download
- Docker-Image mit Healthcheck und `docker-compose.yml`
- Unit-Tests für Parser, Speicher, Filter und Verlauf
- README mit Screenshots aus erfundenen Demodaten (`tools/demo_data.py`)
