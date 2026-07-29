from pathlib import Path
import re
import requests as req
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import geojson
import csv
import json
import argparse
import urllib.parse

GITHUB_OWNER = "JThompson-007"
GITHUB_REPO = "alertreader"
GITHUB_BRANCH = "main"

class XMLWarning:
    def __init__(self, xml, prov, uri, msgType, startTime, expiryTime):
        self.xml = xml
        self.prov = prov
        self.msgType = msgType
        self.uri = uri
        self.startTime = startTime
        self.expiryTime = expiryTime
    xml: ET
    prov: str
    msgType: str
    uri: str
    startTime: datetime    
    expiryTime: datetime

class Alert:

    def __init__(self, location, startUri, startTime, expiryTime, province, jsonLink):
        self.location = location
        self.startUri = startUri
        self.startTime = startTime
        self.endTime = None
        self.expiryTime = expiryTime
        self.province = province
        self.jsonLink = jsonLink

    location: str
    province: str
    startUri: str
    endUri: str
    startTime: datetime
    endTime: datetime
    expiryTime: datetime
    jsonLink: str

# Parse arguments from github actions. --- NOTE: Claude generated code
def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", help="Start hour, format YYYY/MM/DD/HH")
    parser.add_argument("--end", help="End hour, format YYYY/MM/DD/HH")
    args = parser.parse_args()

    if args.start and args.end:
        currentHour = datetime.strptime(args.start, "%Y/%m/%d/%H")
        endHour = datetime.strptime(args.end, "%Y/%m/%d/%H")
    else:
        # Default: sweep all of "yesterday" (UTC) if no args given --
        # this is what runs automatically on the daily schedule.
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        currentHour = datetime(yesterday.year, yesterday.month, yesterday.day, 0)
        endHour = datetime(yesterday.year, yesterday.month, yesterday.day, 23)

    return currentHour, endHour


# Reads in active alerts from the standarized JSON file produced by previous 24h run
def readActiveAlerts(activeAlerts, filename):
    try:
        with open(filename, mode="r") as f:
            alertsDict = json.load(f)

            for alert in alertsDict:
                activeAlerts.append(Alert(
                    location=alert["location"],
                    startUri=alert["startUri"],
                    startTime=datetime.fromisoformat(alert["startTime"]),
                    expiryTime=datetime.fromisoformat(alert["expiryTime"]),
                    province=alert["province"],
                    jsonLink=alert["jsonLink"]
                ))

        # Open file in write mode to clear it; ensures records are removed even if new records aren't written at the end of the period
        with open(filename, mode="w") as f:
            pass
    except (FileNotFoundError, json.JSONDecodeError):
        return

def writeAlertsToJSON(activeAlerts, filename):
    alertList = []
    for alert in activeAlerts:
        alertList.append(
            {"location": alert.location,
            "startUri": alert.startUri,
            "startTime": alert.startTime.isoformat(),
            "expiryTime": alert.expiryTime.isoformat(),
            "province": alert.province,
            "jsonLink": alert.jsonLink}
            )
    
    with open(filename, mode="w") as f:
        json.dump(alertList, f, indent=2)    


def searchAlertList(alertList, location) -> int:
    for i in range(len(alertList)):
        if(alertList[i].location == location):
            return i
    return -1


def generateRepoLink(relativePath) -> str:
    encoded_path = urllib.parse.quote(relativePath)
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{encoded_path}"

def generateGEOJSON(polygonText, currentLocation, startTime) -> str:

    # Generate filename based on location and start time
    jsonPath = f"Archived_Files/Polygons/{startTime.strftime('%Y/%m/%d')}/" + f"{currentLocation}-{startTime.strftime('%H%M%S')}.geojson".replace(" ", "").replace(":", "")

    # Convert the text list of (lat, lon) coordinates to a list of float (lon, lat) to fit GEOJSON format
    points = []
    for coordPair in polygonText.split():
        latString, lonString = coordPair.split(",")
        lat = float(latString)
        lon = float(lonString)
        points.append((lon, lat))
    Path(jsonPath).parent.mkdir(parents=True, exist_ok=True)

    # Create the polygon object and write to the file
    polygon = geojson.Polygon([points])
    with open(jsonPath, mode="w") as f:
        geojson.dump(polygon, f)

    return jsonPath

def downloadCAP(URL, session, startTime, currentLocation, startend) -> str:
    capPath = f"Archived_Files/CAP_Alerts/{startTime.strftime('%Y/%m/%d')}/" + f"{startend}-{currentLocation}-{startTime.strftime('%H%M%S')}.cap".replace(" ", "").replace(":", "")
    # create directory, if needed
    Path(capPath).parent.mkdir(parents=True, exist_ok=True)

    with open(capPath, "w") as f:
        f.write(session.get(URL, timeout=10).text)
    return capPath

# Takes an alert (entire XML) and stores each info block as its own objecct along with the start/expiry times for easy sorting, and the province from the URI
def treeToAlert(alert, prov, URI, allAlerts):
    # Standard namespace for CAP 1.2 XML files
    ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

    # search for the "info" tags, and only continue read the English version to avoid duplicates
    for elm in alert.findall("cap:info", ns):
        if(elm.find("cap:language", ns).text == "en-CA"):
             
             # Only read further if the alert is tornado-related
             if(elm.find("cap:event", ns).text == "tornado"):
                allAlerts.append(XMLWarning(elm, prov, URI, elm.find("cap:responseType", ns).text, datetime.strptime(elm.find("cap:effective", ns).text[:19], "%Y-%m-%dT%H:%M:%S"), datetime.strptime(elm.find("cap:expires", ns).text[:19], "%Y-%m-%dT%H:%M:%S")))


def parseAlerts(finalAlerts, activeAlerts, allAlerts, session):
    # Standard namespace for CAP 1.2 XML files
    ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

    for alert in allAlerts:

        # Read each individual 
        for area in alert.xml.findall("cap:area", ns):
            currentLocation = area.find("cap:areaDesc", ns).text
            index = searchAlertList(activeAlerts, currentLocation)
            # Check for cases where the alert is cancelled --- could be actually getting cancelled (i.e., the alert is on the active list)
            # Could also be a case where the alert is "cancelled" but is not on the active list (a so-called "second cancellation")
            if(index != -1):
                if(alert.startTime == alert.expiryTime or alert.msgType == "AllClear"):
                    # Download the end link under the start time so it gets saved in the same folder even if the start and end times are different dates
                    capPath = generateRepoLink(downloadCAP(alert.uri, session, activeAlerts[index].startTime, currentLocation, "end"))
                    activeAlerts[index].endTime = alert.startTime
                    activeAlerts[index].endUri = capPath
                    #print(f"finishing alert for: {currentLocation} --- link: {alert.uri}")
                    finalAlerts.append(activeAlerts.pop(index))
            else:
                if(alert.startTime != alert.expiryTime and alert.msgType != "AllClear"):
                    
                    # Handle great lakes specific tag, which is always Ontario
                    if(alert.prov == "GL"):
                        alert.prov = "ON"

                    #print(f"creating a new alert for: {currentLocation} --- link: {alert.uri}")

                    # Create GeoJson Polygon for warned area 
                    jsonpath = generateRepoLink(generateGEOJSON(area.find("cap:polygon", ns).text, currentLocation, alert.startTime))
                    capPath = generateRepoLink(downloadCAP(alert.uri, session, alert.startTime, currentLocation, "start"))
                    activeAlerts.append(Alert(currentLocation, capPath, alert.startTime, alert.expiryTime, alert.prov, jsonpath))
                 

def readHourAlerts(date, hour, designation, allAlerts, session):
    # Scrape the HTML page for the hour
    URL = f'https://dd.weather.gc.ca/{date}/WXO-DD/alerts/cap/{date}/{designation}/{hour}/'
    response = session.get(URL, timeout=10)

    # Continue only if the link exists (sometimes no alerts exists for a given hour)
    if(response.status_code != 404):
        # Find all the downloadable CAP links for the hour from HTML
        download_links = re.findall(r'href="([^"?/][^"]*\.cap)"', response.text)

        # Load in each link, parse into tree format for intrepretation
        for link in download_links:
            #print(f'downloading: {URL}{link}...')

            # Grab provinical code from the link string
            prov = link[2:4]

            # Read the actual XML content
            alertContent = session.get(f'{URL}{link}', timeout=10).content

            # Insert into an XML tree and pass that to the parser
            alert = ET.fromstring(alertContent)
            treeToAlert(alert, prov, f'{URL}{link}', allAlerts)

# Run the hourly alert reader for the entire day, for water and land alerts
def readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts, allAlerts):
    
    # Set up session to interact with MSC datamart
    session = req.Session()

    # Loop through each hour in the range
    while(currentHour <= endHour):
        #print(f'Working on --- Date: {currentHour.year}{currentHour.month:02d}{currentHour.day:02d} Hour: {currentHour.hour:02d}')

        # Format the date to match ECCC's directory structure
        datetimeString = f"{currentHour.year}{currentHour.month:02d}{currentHour.day:02d}"

        # Read water and land alerts for the hour
        readHourAlerts(datetimeString, f"{currentHour.hour:02d}", "LAND", allAlerts, session)
        readHourAlerts(datetimeString, f"{currentHour.hour:02d}", "WATR", allAlerts, session)

        # Step forward an hour
        currentHour = currentHour + timedelta(hours=1)

    # Now all of the tornado warning info blocks are stored in the allAlerts list. Sort this list by time, then step through and apply some 
    # logic to parse out individual warning threads for area descriptions

    allAlerts.sort(key = lambda x: x.startTime)

    parseAlerts(finalAlerts, activeAlerts, allAlerts, session)

    # Once all alerts have been read, check if any of the "active" alerts have actually passed their expiry time
    for alert in activeAlerts[:]:
        if(alert.expiryTime < endHour +timedelta(hours=1)):
            alert.endTime = alert.expiryTime
            alert.endUri = "No link - Expired without explicit message" # End URI will just be pinned as the start URI, since there was no explicit end message
            finalAlerts.append(activeAlerts.pop(activeAlerts.index(alert)))

def writeAlertsToCSV(alerts, filename):
    with open(filename, mode='a', newline='') as alertFile:
        csvWriter = csv.writer(alertFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        for alert in alerts:
            csvWriter.writerow([alert.startTime.strftime("%y/%m/%d"), alert.location, alert.province, alert.startTime.strftime("%H:%M"), alert.startUri, alert.endTime.strftime("%H:%M"), alert.endUri, alert.jsonLink])

def main():    

    # Take input from Github Actions
    currentHour, endHour = parse_args()
    #currentHour = datetime.strptime(f"{input('Input the starting date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    #endHour = datetime.strptime(f"{input('Input the ending date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")

    allAlerts = []
    finalAlerts = []
    activeAlerts = []
    readActiveAlerts(activeAlerts, "active_alerts.json")

    readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts, allAlerts)

    #Sort alert list before adding to file, so everything is in chronological order
    finalAlerts.sort(key=lambda x: x.startTime)
    activeAlerts.sort(key=lambda x: x.startTime)

    #print("\n\nFinal Alerts:")
    #for alert in finalAlerts:
    #    print(f"Location: {alert.location}, Start Time: {alert.startTime}, End Time: {alert.endTime}, Province: {alert.province}")    
    #print("\n\nActive Alerts:")
    #for alert in activeAlerts:
    #    print(f"Location: {alert.location}, Start Time: {alert.startTime}, Expiry Time: {alert.expiryTime}, Province: {alert.province}")

    # Save finished alerts to CSV
    writeAlertsToCSV(finalAlerts, "finalAlerts.csv")

    # Save active alerts to a live JSON file that will be read next execution
    writeAlertsToJSON(activeAlerts, "active_alerts.json")

if __name__ == "__main__":
    main()