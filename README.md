This repository contains a Python script to read and store tornado warnings issued by ECCC, and also serves as the permenant home for the data it stores.

The script downloads XML files in CAP format directly from ECCC's MSC Datamart via https requests. Each alert is skimmed, and only relevent tornado warning alerts (start and end for a given location) are stored. Updates are made daily at 12:00 UTC for the previous day from 00:00 UTC to 23:59 UTC. Any alerts that are still active at 23:59 UTC will not be recorded until the following day.
The warning list can be accessed in finalAlerts.csv. Each row also contains links to CAP alert that started and ended the warning, as well as a downloadable GeoJSON file of the warned area polygon (which can also be viewed natively within Github). These files live within Archived_Files folder of this repo.
The finalAlerts.csv file can be downloaded and opened with any spreadsheet software including Excel and Google Sheets.

Following updates to ECCC's warning system that introduced storm-based warning polygons, the reader was overhauled. Part of that overhaul included a migration to a new warning spreadsheet called polygonAlerts.csv. This file serves the same purpose as finalAlerts.csv (which holds warnings prior to August 11th, 2026), with the addtion of serveral columns with additional information.
