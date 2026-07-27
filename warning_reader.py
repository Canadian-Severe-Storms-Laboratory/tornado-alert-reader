import re
import requests as req
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import csv

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

    def __init__(self, location, startUri, startTime, expiryTime, province):
        self.location = location
        self.startUri = startUri
        self.startTime = startTime
        self.endTime = None
        self.expiryTime = expiryTime
        self.province = province

    location: str
    province: str
    uri: str
    startTime: datetime
    endTime: datetime
    expiryTime: datetime


def searchAlertList(alertList, location) -> int:
    for i in range(len(alertList)):
        if(alertList[i].location == location):
            return i
    return -1

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


def parseAlerts(finalAlerts, activeAlerts, allAlerts):
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
                    activeAlerts[index].endTime = alert.startTime
                    activeAlerts[index].endUri = alert.uri
                    print(f"finishing alert for: {currentLocation} --- link: {alert.uri}")
                    finalAlerts.append(activeAlerts.pop(index))
            else:
                if(alert.startTime != alert.expiryTime and alert.msgType != "AllClear"):
                    
                    # Handle great lakes specific tag, which is always Ontario
                    if(alert.prov == "GL"):
                        alert.prov = "ON"

                    print(f"creating a new alert for: {currentLocation} --- link: {alert.uri}")
                    activeAlerts.append(Alert(currentLocation, alert.uri, alert.startTime, alert.expiryTime, alert.prov))
                 

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
            print(f'downloading: {URL}{link}...')

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
        print(f'Working on --- Date: {currentHour.year}{currentHour.month:02d}{currentHour.day:02d} Hour: {currentHour.hour:02d}')

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

    parseAlerts(finalAlerts, activeAlerts, allAlerts)

    # Once all alerts have been read, check if any of the "active" alerts have actually passed their expiry time
    for alert in activeAlerts:
        if(alert.expiryTime < endHour +timedelta(hours=1)):
            alert.endTime = alert.expiryTime
            finalAlerts.append(activeAlerts.pop(activeAlerts.index(alert)))

def writeAlertsToCSV(alerts, filename):
    with open(filename, mode='w', newline='') as alertFile:
        csvWriter = csv.writer(alertFile, delimiter=',', quotechar='"', quoting=csv.QUOTE_MINIMAL)
        for alert in alerts:
            csvWriter.writerow([alert.startTime.strftime("%y/%m/%d"), alert.location, alert.province, alert.startTime.strftime("%H:%M"), alert.endTime.strftime("%H:%M")])

def main():    
    currentHour = datetime.strptime(f"{input('Input the starting date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    endHour = datetime.strptime(f"{input('Input the ending date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    allAlerts = []
    finalAlerts = []
    activeAlerts = []

    readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts, allAlerts)

    # Sort alert list before adding to file, so everything is in chronological order
    finalAlerts.sort(key=lambda x: x.startTime)
    activeAlerts.sort(key=lambda x: x.startTime)

    print("\n\nFinal Alerts:")
    for alert in finalAlerts:
        print(f"Location: {alert.location}, Start Time: {alert.startTime}, End Time: {alert.endTime}, Province: {alert.province}")    
    print("\n\nActive Alerts:")
    for alert in activeAlerts:
        print(f"Location: {alert.location}, Start Time: {alert.startTime}, Expiry Time: {alert.expiryTime}, Province: {alert.province}")

    
    #writeAlertsToCSV(finalAlerts, "finalAlerts.csv")


if __name__ == "__main__":
    main()