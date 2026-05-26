Hier könnt ihr mit einem Raspberry Pi oder ähnlichem einen Universal Spritcomputer bauen. 
Mit diversen Sensoren könnt ihr Spritverbrauch, Aussentemperatur, Gps Geschwindigkeit und Voltzahl der Autobatterie messen und anzeigen lassen. 

Teile die ich benutzt habe:
-Raspberry pi 3b
-Gehäuse für Raspberry pi 3b https://www.amazon.de/dp/B07NTH1JWH?ref=ppx_yo2ov_dt_b_fed_asin_title&tag=psblog-21 [Anzeige]
- Display
-Spritgeber https://www.shop.flowtrecs.com/de/hauptseite/…sion-s_20_60_ps
-Gps Maus https://de.aliexpress.com/item/3299...t_main.11.1d6e5c5fVnpwPn&gatewayAdapt=glo2deu
-Tempsensor https://www.ebay.de/itm/285566359578

Pins für den Spritgeber sind:

Spitze (Tip) → Signal (Impuls-Ausgang)
Ring → +5 V Versorgung
Schaft (Sleeve)→ Masse (GND)

Am Raspberry:

    GPIO (BCM): 12
    Physischer Pin am Raspberry Pi: Pin 32



Funktionsübersicht des Universal Sprit Computers​

    Geschwindigkeitsanzeige

    Aktuelle Geschwindigkeit in km/h, direkt aus dem GPS.

2. Durchschnittsgeschwindigkeit

    Ø-Geschwindigkeit für die aktuelle Fahrt.
    Ø-Geschwindigkeit für den aktuellen Tag.

3. Momentanverbrauch

    Aktueller Verbrauch in l/100 km (gefiltert, Ausreißerunterdrückung).

4. Durchschnittsverbrauch

    Ø-Verbrauch in l/100 km für die aktuelle Fahrt.
    Ø-Verbrauch in l/100 km für den aktuellen Tag.

5. Verbrauch in l/h

    Anzeige des momentanen Verbrauchs in Litern pro Stunde – ideal im Stand / bei langsamer Fahrt.

6. Streckenmessung (Trip & Tag)

    Gefahrene Kilometer seit letztem Trip-Reset.
    Gefahrene Kilometer seit letztem Dayreset (Tagesfahrt).

7. Zeitmessung

    Fahrzeit seit Trip-Start (Trip Time).
    Fahrzeit seit Tagesbeginn (Day Time).

8 .Tank- und Reichweitenanzeige

    Grafische Tankanzeige mit Haupttank + Reservebereich, abgestuft eingefärbt.
    Anzeige in Litern oder geschätzter Restreichweite in km (umschaltend).
    Berücksichtigung von Tankinhalt und Reserve (konfigurierbare Kapazität im Code).

9. Sichere Datenspeicherung mit Backup

    Fahrdaten (Trip, Tag, Verbräuche, Zeit usw.) werden regelmäßig sicher in eine JSON-Datei geschrieben.
    Automatisches Backup der letzten Version.
    Beim Start:
    Prüft auf Datenfehler oder leere Dateien.
    Fragt nur dann: „Datenfehler erkannt, Backup laden?“.
    Kann Daten aus Backup wiederherstellen oder komplett zurücksetzen.

10. Temperaturüberwachung (Außen + Öl)

    Unterstützung für zwei DS18B20-Sensoren:
    Sensor 1: Außentemperatur.
    Sensor 2: Öltemperatur.
    Pufferspeicher: Kurzzeitige Aussetzer werden abgefangen, es bleibt der letzte brauchbare Wert stehen (bis 15 s).
    Trendpfeile an der Öltemperatur:
    Steigend: Pfeil nach oben.
    Fallend: Pfeil nach unten.
    Stabil: kein Pfeil.
    Farbwarnung Öl:
    Ab 100 °C gelb.
    Ab 110 °C rot.

11. Bordspannung (Volt)

    Messung über INA219 (falls angeschlossen).
    Anzeige der aktuellen Bordspannung in Volt.

12. Satelliten- / GPS-Status

    Anzeige genutzter / gesehener Satelliten.
    Klarer Hinweis, ob GPS-Fix vorhanden ist (Geschwindigkeit -- bei fehlendem Fix).

13. Kompensation von GPS-Aussetzern

    Erkennung von GPS-Verlust und Wiederkehr.
    Nachträgliche Distanzkorrektur zwischen letzter und neuer Position (mit Begrenzung auf sinnvolle Werte), um Sprünge zu vermeiden.

14. Tages-/Trip-Reset-Funktionen

    Dayreset-Button für Tagesdaten (Verbrauch, Strecke, Zeiten, Ø-Werte).
    Reset-Button für Tripdaten (Tankfüllung auf Ausgangsstand, Verbräuche, Strecken, Zeiten).

15. Nacht- / Tagmodus (Augenfreundliche Darstellung)

    Umschaltbarer Nachtmodus per Checkbox im Config-Menü.
    Hintergrund immer schwarz (blendarmer Grund).
    Tag/Nacht unterscheiden sich über:
    Textfarben (hell/kontrastreich vs. sanft/blau).
    Abgedunkelte, nicht blendende Button-Farben im Nachtmodus.
    Gedämpfte Farben in der Tankanzeige.

16. Individuelle Anzeige-Konfiguration

    Config-Fenster mit Checkboxen zum Ein-/Ausblenden von:
    Geschwindigkeit
    Durchschnittsgeschwindigkeit
    Momentanverbrauch
    Durchschnittsverbrauch
    l/h + Volt
    Trip-Anzeige
    Temperaturen
    Satellitenanzeige
    Trip-Zeit
    Tageszeit / Datum
    Einstellung wird in einer Config-Datei gespeichert und beim Start geladen.

17. Temperatur-Sensor-Tausch per Klick

    Ein Klick auf die Temperaturzeile tauscht die Zuordnung:
    Erkannt vertauschte Sensoren (Außen/Öl) können softwareseitig umgedreht werden.

18. Komfortable Bedienoberfläche

    Vollbild-GUI für den Raspberry-Pi-Touchscreen.
    Klare, große Schriftarten für schnelle Ablesbarkeit im Fahrzeug.
    Mauszeiger ausgeblendet (Dashboard-Feeling).

19. Automatische Update-Funktion

    Prüfung einer Online-Version über version.txt.
    „Jetzt aktualisieren“-Button im Config-Menü.
    Externer updater.py:
    Lädt sprit.py, updater.py und version.txt direkt von GitHub.
    Erstellt Backups der alten Dateien.
    Nach erfolgreichem Update: Hinweis-Fenster mit „Restart Skript“-Button.

20. Robust gegen harte Ausschaltungen

    Speichern über Temp-Datei + fsync + atomare Ersetzung.
    Backup-Mechanismus, um Daten nach Stromausfall möglichst vollständig wiederherzustellen.
    Backup-Dialog nur, wenn wirklich etwas mit der Hauptdatei nicht stimmt.

