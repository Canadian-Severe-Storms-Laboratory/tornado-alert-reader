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

    def __init__(self, location, startUri, startTime, expiryTime, province, polygonPoints):
        self.location = location
        self.startUri = startUri
        self.startTime = startTime
        self.endTime = None
        self.expiryTime = expiryTime
        self.province = province
        self.polygonPoints = polygonPoints
        self.jsonLink = ""

    location: str
    province: str
    startUri: str
    endUri: str
    startTime: datetime
    endTime: datetime
    expiryTime: datetime
    polygonPoints: list[list[tuple[float, float]]] # List of a list of coordinates (lon, lat) for each polygon in the alert
    jsonLink: str


# Finds the provincial code based on the "alert coverage" parameter which describes a general region
def identifyProvince(provincialStr) -> str:
    if(re.search("Manitoba", provincialStr) != None):
        return "MB"
    if(re.search("Saskatchewan", provincialStr) != None):
        return "SK"
    if(re.search("Ontario", provincialStr) != None):
        return "ON"
    if(re.search("Alberta", provincialStr) != None):
        return "AB"
    if(re.search("Quebec", provincialStr) != None):
        return "QC"
    if(re.search("British Columbia", provincialStr) != None):
        return "BC"
    if(re.search("New Brunswick", provincialStr) != None):
        return "NB"
    if(re.search("Nova Scotia", provincialStr) != None):
        return "NS"
    if(re.search("Prince Edward Island", provincialStr) != None):
        return "P"
    if((re.search("Newfoundland", provincialStr) != None)):
        return "NL"
    if(re.search("Yukon", provincialStr) != None):
        return "YT"
    if(re.search("Northwest Territories", provincialStr) != None):
        return "NT"
    if(re.search("Nunavut", provincialStr) != None):
        return "NU"
    # Special case for a few Ontario lakes
    if(re.search("Lake of the Woods Lake Nipigon North Channel Lake Nipissing and Lake Simcoe", provincialStr) != None):
        return "ON"
    return ""
        
# NOTE: Claude generated code
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
        with open(filename, mode="r") as f:
            alertsDict = json.load(f)

            for alert in alertsDict:
                activeAlerts.append(Alert(
                    location=alert["location"],
                    startUri=alert["startUri"],
                    startTime=datetime.fromisoformat(alert["startTime"]),
                    expiryTime=datetime.fromisoformat(alert["expiryTime"]),
                    province=alert["province"],
                    polygonPoints=alert["polygon"]
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
            "polygon": alert.polygonPoints}
            )
    
    with open(filename, mode="w") as f:
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
    if(relativePath == "Expired without explicit message"):
        return relativePath

    encoded_path = urllib.parse.quote(relativePath)
    return f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/blob/{GITHUB_BRANCH}/{encoded_path}"

def generatePolygon(polygonText) -> list[float]:
    # Convert the text list of (lat, lon) coordinates to a list of float (lon, lat) to fit GEOJSON format
    points = []
    for coordPair in polygonText.split():
        latString, lonString = coordPair.split(",")
        lat = float(latString)
        lon = float(lonString)
        points.append((lon, lat))
    return points

def generateGEOJSON(polygons, currentLocation, startTime) -> str:

    # Generate filename based on location and start time
    jsonPath = f"Watches/Archived_Files/Polygons/{startTime.strftime('%Y/%m/%d')}/" + f"{currentLocation[:20]}-{startTime.strftime('%H%M%S')}.geojson".replace(" ", "").replace(":", "")
    #print(f"generating geojson for: {currentLocation}")
    
    Path(jsonPath).parent.mkdir(parents=True, exist_ok=True)

    # Create the multipolygon object and write to the file
    multipolygon_coords = [[ring] for ring in polygons]
    multipolygon = geojson.MultiPolygon(multipolygon_coords)
    with open(jsonPath, mode="w") as f:
        geojson.dump(multipolygon, f)

    return jsonPath

def downloadCAP(URL, session, startTime, currentLocation, startend) -> str:

    if(URL == "Expired without explicit message"):
        return URL

    capPath = f"Watches/Archived_Files/CAP_Alerts/{startTime.strftime('%Y/%m/%d')}/" + f"{startend}-{currentLocation[:20]}-{startTime.strftime('%H%M%S')}.cap".replace(" ", "").replace(":", "")
    # create directory, if needed
    Path(capPath).parent.mkdir(parents=True, exist_ok=True)

    with open(capPath, "w") as f:
        f.write(session.get(URL, timeout=10).text)
    return capPath

# Takes an alert (entire XML) and stores each info block as its own objecct along with the start/expiry times for easy sorting, and the province from the URI
def treeToAlert(alert, URI, allAlerts):
    # Standard namespace for CAP 1.2 XML files
    ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

    # search for the "info" tags, and only continue read the English version to avoid duplicates
    for elm in alert.findall("cap:info", ns):
        if(elm.find("cap:language", ns).text == "en-CA"):
             
             # Only read further if the alert is tornado-related
             if(elm.find("cap:event", ns).text == "tornado"):

                prov = ""
                # Get the provincial code
                for parameter in elm.findall("cap:parameter", ns):
                     if(parameter.find("cap:valueName", ns).text == "layer:EC-MSC-SMC:1.0:Alert_Coverage"):
                          prov = identifyProvince(parameter.find("cap:value", ns).text)
                
                #print("Tornado watch found!")
                allAlerts.append(XMLWarning(elm, prov, URI, elm.find("cap:responseType", ns).text, datetime.strptime(elm.find("cap:effective", ns).text[:19], "%Y-%m-%dT%H:%M:%S"), datetime.strptime(elm.find("cap:expires", ns).text[:19], "%Y-%m-%dT%H:%M:%S")))

# Merges all watches issued and ended at the same time into a single alert. Also downloads the CAP files and GeoJSONs polygons for each alert
def mergeWatches(finalAlerts, finalMergedAlerts, session):
    grouped = {}   # will map (startUri, endUri) -> the FIRST Alert object seen for that pair
    order = []     # remembers the order keys were first added, so output stays in original order

    for alert in finalAlerts:
        key = (alert.startUri, alert.endUri)

        if key not in grouped:
            # First time we've seen this exact (startUri, endUri) combo.
            grouped[key] = alert
            order.append(key)
        else:
            # We've already stored an alert for this (startUri, endUri) pair, tack on polygon points to this existing alert
            grouped[key].polygonPoints.append(alert.polygonPoints[0])
            grouped[key].location += f"- {alert.location}"  # Concatenate the location names for the merged alert

    for finalKey in order:
        finalMergedAlerts.append(grouped[finalKey])

    finalMergedAlerts.sort(key=lambda a: (a.startTime, a.endTime))

    # Now download the necessary CAP and GeoJSON files - Also alter the URLs saved to the alerts to reflect the github link
    for alert in finalMergedAlerts:
        # Download the CAP files for the start and end of the alert
        alert.startUri = generateRepoLink(downloadCAP(alert.startUri, session, alert.startTime, alert.location, "start"))
        alert.endUri = generateRepoLink(downloadCAP(alert.endUri, session, alert.startTime, alert.location, "end"))

        # Generate the GeoJSON file for the polygon and save the link to the alert
        alert.jsonLink = generateRepoLink(generateGEOJSON(alert.polygonPoints, alert.location, alert.startTime))
        


def parseAlerts(finalAlerts, activeAlerts, allAlerts):
    # Standard namespace for CAP 1.2 XML files
    ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}

    for alert in allAlerts:

        # Read each individual 
        for area in alert.xml.findall("cap:area", ns):
            currentLocation = area.find("cap:areaDesc", ns).text
            # print(f"At: {alert.startTime} tracking for this area: {currentLocation}")
            index = searchAlertList(activeAlerts, currentLocation)
            # Check for cases where the alert is cancelled --- could be actually getting cancelled (i.e., the alert is on the active list)
            # Could also be a case where the alert is "cancelled" but is not on the active list (a so-called "second cancellation")
            if(index != -1):
                if(alert.startTime == alert.expiryTime or alert.msgType == "AllClear"):
                    # Download the end link under the start time so it gets saved in the same folder even if the start and end times are different dates
                    activeAlerts[index].endTime = alert.startTime
                    activeAlerts[index].endUri = alert.uri
                    # print(f"finishing alert for: {currentLocation} --- link: {alert.uri}")
                    finalAlerts.append(activeAlerts.pop(index))
                # Not a new warning, but not ending. Update the expiry time
                else:
                    activeAlerts[index].expiryTime = alert.expiryTime
                    #print(f"updating for: {currentLocation}")
            else:
                if(alert.startTime != alert.expiryTime and alert.msgType != "AllClear"):
                    # print(f"creating a new alert for: {currentLocation} --- link: {alert.uri}")
                    # Create Polygon for warned area 
                    polygon = [list[list[tuple[float, float]]]]
                    polygon[0] = generatePolygon(area.find("cap:polygon", ns).text)
                    activeAlerts.append(Alert(currentLocation, alert.uri, alert.startTime, alert.expiryTime, alert.prov, polygon))
                 

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
            # Read the actual XML content
            try:
                alertContent = session.get(f'{URL}{link}', timeout=20).content
            except req.exceptions.RequestException as e:
                continue
            # Insert into an XML tree and pass that to the parser
            alert = ET.fromstring(alertContent)
            treeToAlert(alert, f'{URL}{link}', allAlerts)

def findOffices(date, session):
    URL = f'https://dd.weather.gc.ca/{date}/WXO-DD/alerts/cap/{date}'
    try:
        response = session.get(URL, timeout=20)   # 10 -> 20
    except req.exceptions.RequestException as e:
        return

    # Scrape HTML and find all the options starting with 'C' (only offices do this, not LAND/WATR)
    codes = re.findall(r'href="(C[^"]*)/"', response.text)
    return codes   

# Run the hourly alert reader for the entire day, for water and land alerts
def readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts, allAlerts, finalMergedAlerts):
    
    # Set up session to interact with MSC datamart
    session = build_session()
    codes = [] # List of office codes that published alerts for the day

    # Loop through each hour in the range
    while(currentHour <= endHour):
        #print(f'Working on --- Date: {currentHour.year}{currentHour.month:02d}{currentHour.day:02d} Hour: {currentHour.hour:02d}')

        # Format the date to match ECCC's directory structure
        datetimeString = f"{currentHour.year}{currentHour.month:02d}{currentHour.day:02d}"
        if(currentHour.hour == 0):
            codes = findOffices(datetimeString, session)

        for code in codes:
            # Read alerts for the hour for each office
            readHourAlerts(datetimeString, f"{currentHour.hour:02d}", f"{code}", allAlerts, session)

        # Step forward an hour
        currentHour = currentHour + timedelta(hours=1)

    # Now all of the tornado warning info blocks are stored in the allAlerts list. Sort this list by time, then step through and apply some 
    # logic to parse out individual warning threads for area descriptions

    allAlerts.sort(key = lambda x: x.startTime)

    parseAlerts(finalAlerts, activeAlerts, allAlerts)

    # Once all alerts have been read, check if any of the "active" alerts have actually passed their expiry time
    for alert in activeAlerts[:]:
        if(alert.expiryTime < endHour +timedelta(hours=1)):
            alert.endTime = alert.expiryTime
            alert.endUri = "Expired without explicit message" # End URI will just be pinned as the start URI, since there was no explicit end message
            finalAlerts.append(activeAlerts.pop(activeAlerts.index(alert)))

    # Perform a merge on watches and put all issued and cancelled together in the same alert message
    mergeWatches(finalAlerts, finalMergedAlerts, session)


def writeAlertsToCSV(alerts, filename):
    with open(filename, mode='a', newline='') as alertFile:
        csvWriter = csv.writer(alertFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        for alert in alerts:
            csvWriter.writerow([alert.startTime.strftime("%y/%m/%d"), alert.location, alert.province, alert.startTime.strftime("%H:%M"), alert.startUri, alert.endTime.strftime("%H:%M"), alert.endUri, alert.jsonLink])

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

    # currentHour = datetime.strptime(f"{input('Input the starting date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    # endHour = datetime.strptime(f"{input('Input the ending date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")

    allAlerts = []
    finalAlerts = []
    activeAlerts = []
    finalMergedAlerts = []
    readActiveAlerts(activeAlerts, "Watches/active_watches.json")

    readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts, allAlerts, finalMergedAlerts)

    #Sort active alert list before adding to file, so everything is in chronological order
    activeAlerts.sort(key=lambda x: x.startTime)

    #print("\n\nFinal Alerts:")
    #for alert in finalAlerts:
    #    print(f"Location: {alert.location}, Start Time: {alert.startTime}, End Time: {alert.endTime}, Province: {alert.province}")    
    #print("\n\nActive Alerts:")
    #for alert in activeAlerts:
    #    print(f"Location: {alert.location}, Start Time: {alert.startTime}, Expiry Time: {alert.expiryTime}, Province: {alert.province}")

    # Save finished alerts to CSV
    writeAlertsToCSV(finalMergedAlerts, "Watches/finalWatches.csv")

    # Save active alerts to a live JSON file that will be read next execution
    writeAlertsToJSON(activeAlerts, "Watches/active_watches.json")

    # Write to the live Google Sheet
    try:
        writeAlertsToGoogleSheet(finalMergedAlerts, get_google_sheet("Watch"))
    except Exception as e:
        print(f"Error occurred while writing to Google Sheet: {e}")

if __name__ == "__main__":
    main()