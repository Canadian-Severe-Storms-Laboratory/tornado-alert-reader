This repository contains a Python script to read and store tornado warnings issued by ECCC, and also serves as the permenant home for the data it stores.

The script downloads XML files in CAP format directly from ECCC's MSC Datamart via https requests. Each alert is skimmed, and only relevent tornado warning alerts (start and end for a given location) is stored.
The warning list can be accessed in finalAlerts.csv. Each row also contains links to CAP alert that started and ended the warning, as well as a downloadable GeoJSON file of the warned area polygon. These files also live within this repo.
The finalAlerts.csv file can be downloaded and opened with any spreadsheet software including Excel and Google Sheets.
