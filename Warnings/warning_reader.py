import os
from pathlib import Path
import re
import gspread
import requests as req
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import geojson
import csv
import json
import argparse
import urllib.parse
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from google.oauth2.service_account import Credentials

GITHUB_OWNER = "Canadian-Severe-Storms-Laboratory"
GITHUB_REPO = "tornado-alert-reader"
GITHUB_BRANCH = "main"


class XMLWarning:
    def __init__(self, xml, prov, uri, msgType, id, responseType, references, startTime, expiryTime, sentTime):
        self.xml = xml
        self.prov = prov
        self.msgType = msgType
        self.id = id
        self.responseType = responseType
        self.references = references
        self.uri = uri
        self.startTime = startTime
        self.expiryTime = expiryTime
        self.sentTime = sentTime
        self.isMinor = False

    xml: ET
    prov: str
    msgType: str
    id: str
    responseType: str
    references: str
    uri: str
    startTime: datetime    
    expiryTime: datetime
    sentTime: datetime
    isMinor: bool

class Alert:

    def __init__(self, location, startUri, startTime, expiryTime, province, jsonLink, stormGeometry, stormMotion, id):
        self.location = location
        self.startUri = startUri
        self.startTime = startTime
        self.endTime = None
        self.expiryTime = expiryTime
        self.stormGeometry = stormGeometry
        self.stormMotion = stormMotion
        self.province = province
        self.jsonLink = jsonLink
        self.id = id

    location: str
    province: str
    startUri: str
    endUri: str
    startTime: datetime
    endTime: datetime
    expiryTime: datetime
    jsonLink: str
    stormGeometry: str
    stormMotion: str
    id: str



def build_session():
    session = req.Session()
    retries = Retry(
        total=3,                     # retry up to 3 times per request
        backoff_factor=1,            # waits 1s, then 2s, then 4s between attempts
        status_forcelist=[500, 502, 503, 504],  # retry on these server-error responses
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session

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
        with open(filename, mode="r", encoding="utf-8") as f:
            alertsDict = json.load(f)

            for alert in alertsDict:
                activeAlerts.append(Alert(
                    location=alert["location"],
                    startUri=alert["startUri"],
                    startTime=datetime.fromisoformat(alert["startTime"]),
                    expiryTime=datetime.fromisoformat(alert["expiryTime"]),
                    province=alert["province"],
                    jsonLink=alert["jsonLink"],
                    stormGeometry=alert["stormGeometry"],
                    stormMotion=alert["stormMotion"],
                    id=alert["id"]
                ))

        # Open file in write mode to clear it; ensures records are removed even if new records aren't written at the end of the period
        with open(filename, mode="w", encoding="utf-8") as f:
            pass
    except (FileNotFoundError, json.JSONDecodeError):
        return

def writeAlertsToJSON(activeAlerts, filename):
    #print("Here! Json")
    alertList = []
    for alert in activeAlerts:
        alertList.append(
            {"location": alert.location,
            "startUri": alert.startUri,
            "startTime": alert.startTime.isoformat(),
            "expiryTime": alert.expiryTime.isoformat(),
            "province": alert.province,
            "jsonLink": alert.jsonLink,
            "stormGeometry": alert.stormGeometry,
            "stormMotion": alert.stormMotion,
            "id": alert.id}
            )
    
    with open(filename, mode="w", encoding="utf-8") as f:
        if not alertList:
            f.write("[]")
        else:
            json.dump(alertList, f, indent=2)    


def searchAlertList(alertList, location) -> int:
    for i in range(len(alertList)):
        if(alertList[i].location == location):
            return i
    return -1


def generateRepoLink(relativePath) -> str:
    encoded_path = urllib.parse.quote(relativePath)
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{encoded_path}"

def generateGEOJSON(polygonList, currentLocation, prov, startTime) -> str:
    # Generate filename based on location and start time
    jsonPath = f"Warnings/Archived_Files/Polygons/{startTime.strftime('%Y/%m/%d')}/" + f"{currentLocation}-{prov}-{startTime.strftime('%H%M%S')}.geojson".replace(" ", "").replace(":", "")
    # Convert the text list of (lat, lon) coordinates to a list of float (lon, lat) to fit GEOJSON format
    polygons = []
    for polygonText in polygonList:
        points = []
        for coordPair in polygonText.split():
            latString, lonString = coordPair.split(",")
            lat = float(latString)
            lon = float(lonString)
            points.append((lon, lat))
        polygons.append([points])

    Path(jsonPath).parent.mkdir(parents=True, exist_ok=True)

    # Create the polygon object and write to the file
    multiPolygon = geojson.MultiPolygon(polygons)
    with open(jsonPath, mode="w", encoding="utf-8") as f:
        geojson.dump(multiPolygon, f)

    return jsonPath

def downloadCAP(URL, session, startTime, currentLocation, prov, startend) -> str:
    capPath = f"Warnings/Archived_Files/CAP_Alerts/{startTime.strftime('%Y/%m/%d')}/" + f"{startend}-{currentLocation}-{prov}-{startTime.strftime('%H%M%S')}.cap".replace(" ", "").replace(":", "")
    # create directory, if needed
    Path(capPath).parent.mkdir(parents=True, exist_ok=True)

    with open(capPath, "w", encoding="utf-8") as f:
        f.write(session.get(URL, timeout=10).text)
    return capPath

# Takes an alert (entire XML) and stores each info block as its own object along with the start/expiry times for easy sorting, and the province from the URI
def treeToAlert(alert, prov, URI, allAlerts):
    # Standard namespace for CAP 1.2 XML files
    ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

    id = alert.find("cap:identifier", ns).text
    msgType = alert.find("cap:msgType", ns).text
    references = None
    sentTime = datetime.strptime(alert.find("cap:sent", ns).text[:19], "%Y-%m-%dT%H:%M:%S")

    if(alert.find("cap:references", ns) != None):
        references = alert.find("cap:references", ns).text

    # search for the "info" tags, and only continue read the English version to avoid duplicates
    for elm in alert.findall("cap:info", ns):
        if(elm.find("cap:language", ns).text == "en-CA"):
             
             # Only read further if the alert is tornado-related
             if(elm.find("cap:event", ns).text == "tornado"):
                #print("Tornado warning found!")
                allAlerts.append(XMLWarning(elm, prov, URI, msgType, id, elm.find("cap:responseType", ns).text, references, datetime.strptime(elm.find("cap:effective", ns).text[:19], "%Y-%m-%dT%H:%M:%S"), datetime.strptime(elm.find("cap:expires", ns).text[:19], "%Y-%m-%dT%H:%M:%S"), sentTime))

def findPolygons(alert, ns) -> list[str]:
    polygonList = []
    for area in alert.xml.findall("cap:area", ns):
        if(area.find("cap:areaDesc", ns).text == "new active threat area" or area.find("cap:areaDesc", ns).text == "continued active threat area"):
            for polygon in area.findall("cap:polygon", ns):
                polygonList.append(polygon.text)
    return polygonList

def parseAlerts(finalAlerts, activeAlerts, allAlerts, session):
    # Standard namespace for CAP 1.2 XML files
    ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
    for alert in allAlerts:
        locations, stormGeometry, stormMotion = None, None, None

        # Get attributes about the warning (locations, geometry, motion)
        for parameter in alert.xml.findall("cap:parameter", ns):
            match(parameter.find("cap:valueName", ns).text):
                case "profile:CAP-CP:0.4:MinorChange":
                    if(parameter.find("cap:value", ns).text == "text"):

                        # flag all info blocks within the same message as minor textual changes as well
                        for otherAlert in allAlerts:
                            if(alert.id == otherAlert.id):
                                otherAlert.isMinor = True

                case "layer:EC-MSC-SMC:1.1:Storm_Position_Description":
                    locations = parameter.find("cap:value", ns).text
                case "layer:EC-MSC-SMC:1.1:Storm_Geometry_Type":
                    stormGeometry = parameter.find("cap:value", ns).text
                case "layer:EC-MSC-SMC:1.1:Motion_Description":
                    stormMotion = parameter.find("cap:value", ns).text

        # Skip updates that are just minor textual updates
        if(alert.isMinor):
            # print(f"flagging: {alert.id} for minor textual changes")
            continue
        
        # New string of warnings, not an update to existing warnings.
        if(alert.msgType == "Alert"):

            polygonList = findPolygons(alert, ns)
            jsonpath = generateRepoLink(generateGEOJSON(polygonList, locations, alert.prov, alert.startTime))
            activeAlerts.append(Alert(locations, alert.uri, alert.startTime, alert.expiryTime, alert.prov, jsonpath, stormGeometry, stormMotion, id=alert.id))

        # An existing chain of warning(s) already exists. This warning will cancel the previous one and start a new one
        elif(alert.msgType == "Update"):

            # Find previous warning in the chain
            for activeAlert in activeAlerts:
                if(activeAlert.id in alert.references):
                    # Add the old warning to the final list, and remove it from the active list
                    activeAlert.endTime = alert.startTime
                    activeAlert.startUri = generateRepoLink(downloadCAP(activeAlert.startUri, session, activeAlert.startTime, activeAlert.location, activeAlert.province, "start"))
                    # NOTE: The end link of this warning is the SAME as the start link for the new warning. They will be saved seperately for clarity since it is a relatively small cost
                    activeAlert.endUri = generateRepoLink(downloadCAP(alert.uri, session, activeAlert.startTime, activeAlert.location, activeAlert.province, "end"))
                    finalAlerts.append(activeAlerts.pop(activeAlerts.index(activeAlert)))

            # If the new warning is not an "AllClear" or a cancellation, then add it to the active list as a new warning
            if(alert.startTime != alert.expiryTime and alert.responseType != "AllClear"):
                polygonList = findPolygons(alert, ns)
                jsonpath = generateRepoLink(generateGEOJSON(polygonList, locations, alert.prov, alert.startTime))
                activeAlerts.append(Alert(locations, alert.uri, alert.startTime, alert.expiryTime, alert.prov, jsonpath, stormGeometry, stormMotion, alert.id))

def readHourAlerts(date, hour, designation, allAlerts, session):
    # Scrape the HTML page for the hour
    URL = f'https://dd.weather.gc.ca/{date}/WXO-DD/alerts/cap/{date}/{designation}/{hour}/'
    try:
        response = session.get(URL, timeout=20)   # 10 -> 20
    except req.exceptions.RequestException as e:
        return

    # Continue only if the link exists (sometimes no alerts exists for a given hour)
    if(response.status_code != 404):
        # Find all the downloadable CAP links for the hour from HTML
        download_links = re.findall(r'href="([^"?/][^"]*\.cap)"', response.text)

        # Load in each link, parse into tree format for intrepretation
        for link in download_links:
            #print(f'downloading: {URL}{link}...')

            # Grab provinical code from the link string
            prov = link[2:4]
            if(prov == "GL"):
                prov = "ON" # Great Lakes warnings are always Ontario

            # Read the actual XML content
            try:
                alertContent = session.get(f'{URL}{link}', timeout=20).content
            except req.exceptions.RequestException as e:
                continue
            # Insert into an XML tree and pass that to the parser
            alert = ET.fromstring(alertContent)
            treeToAlert(alert, prov, f'{URL}{link}', allAlerts)

# Run the hourly alert reader for the entire day, for water and land alerts
def readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts, allAlerts):
    
    # Set up session to interact with MSC datamart
    session = build_session()

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

    allAlerts.sort(key = lambda x: x.sentTime)

    parseAlerts(finalAlerts, activeAlerts, allAlerts, session)

    # Once all alerts have been read, check if any of the "active" alerts have actually passed their expiry time
    for alert in activeAlerts[:]:
        if(alert.expiryTime < endHour +timedelta(hours=1)):
            alert.endTime = alert.expiryTime
            alert.endUri = "No link - Expired without explicit message" # End URI will just be pinned as the start URI, since there was no explicit end message
            finalAlerts.append(activeAlerts.pop(activeAlerts.index(alert)))

def writeAlertsToCSV(alerts, filename):
    #print("here! (CSV)")
    with open(filename, mode='a', newline='', encoding="utf-8") as alertFile:
        csvWriter = csv.writer(alertFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        for alert in alerts:
            csvWriter.writerow([alert.startTime.strftime("%y/%m/%d"), alert.location, alert.stormGeometry, alert.stormMotion, alert.province, alert.startTime.strftime("%H:%M"), alert.startUri, alert.endTime.strftime("%H:%M"), alert.endUri, alert.jsonLink])

def get_google_sheet(sheetName):
    creds_json = os.environ["GOOGLE_SHEETS_CREDENTIALS"]  # set via GitHub Actions secret
    GOOGLE_SHEETS_ID = os.environ["GOOGLE_SHEETS_ID"]  # set via GitHub Actions secret
    creds_dict = json.loads(creds_json)
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(GOOGLE_SHEETS_ID).worksheet(sheetName)

def writeAlertsToGoogleSheet(alerts, sheet):
    rows = []
    for alert in alerts:
        rows.append([
            "",
            alert.startTime.strftime("%y/%m/%d"),
            alert.location,
            alert.stormGeometry,
            alert.stormMotion,
            alert.province,
            alert.startTime.strftime("%H:%M"),
            alert.startUri,
            alert.endTime.strftime("%H:%M"),
            alert.endUri,
            alert.jsonLink
        ])

    # Only write if rows aren't empty to prevent gspread from throwing an error    
    if rows:
        sheet.append_rows(rows, value_input_option="USER_ENTERED", insert_data_option="INSERT_ROWS")


def main():    

    # Take input from Github Actions
    currentHour, endHour = parse_args()
    #currentHour = datetime.strptime(f"{input('Input the starting date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    #endHour = datetime.strptime(f"{input('Input the ending date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")

    allAlerts = []
    finalAlerts = []
    activeAlerts = []
    readActiveAlerts(activeAlerts, "Warnings/active_warnings.json")

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
    writeAlertsToCSV(finalAlerts, "Warnings/finalWarnings.csv")

    try:
        writeAlertsToGoogleSheet(finalAlerts, get_google_sheet("Warning"))
    except Exception as e:
        print(f"Error occurred while writing to Google Sheet: {e}")

    # Save active alerts to a live JSON file that will be read next execution
    writeAlertsToJSON(activeAlerts, "Warnings/active_warnings.json")

if __name__ == "__main__":
    main()
