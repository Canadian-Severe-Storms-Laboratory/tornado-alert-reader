import re
import requests as req
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta


class Alert:

    def __init__(self, location, startTime, expiryTime):
        self.location = location
        self.startTime = startTime
        self.endTime = None
        self.expiryTime = expiryTime

    location: str
    startTime: datetime
    endTime: datetime
    expiryTime: datetime

def searchAlertList(alertList, location) -> int:
    for i in range(len(alertList)):
        if(alertList[i].location == location):
            return i
    return -1

# Return true if the warning is a tornado warning, false otherwise
def parseLoadedAlert(alert, finalAlerts, activeAlerts):
    ns = {"cap": "urn:oasis:names:tc:emergency:cap:1.2"}
    for elm in alert.findall("cap:info", ns):
        if(elm.find("cap:language", ns).text == "en-CA"):
             if(elm.find("cap:event", ns).text == "tornado"):
                print("Tornado warning found!")
                for area in elm.findall("cap:area", ns):
                    currentLocation = area.find("cap:areaDesc", ns).text
                    currentEffectiveTime = datetime.strptime(elm.find("cap:effective", ns).text[:16], "%Y-%m-%dT%H:%M")
                    currentExpiryTime = datetime.strptime(elm.find("cap:expires", ns).text[:16], "%Y-%m-%dT%H:%M")
                    index = searchAlertList(activeAlerts, currentLocation)
                    # If the alert is not already in the list, add it to the active alerts list
                    if(index == -1):
                        activeAlerts.append(Alert(currentLocation, currentEffectiveTime, currentExpiryTime))
                    else:
                        activeAlerts[index].endTime = currentExpiryTime
                        if(currentExpiryTime == currentEffectiveTime):
                            finalAlerts.append(activeAlerts.pop(index)) 
                 
                

             

def readHourAlerts(date, hour, designation, finalAlerts, activeAlerts, session):
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
            alertContent = session.get(f'{URL}{link}', timeout=10).content
            alert = ET.fromstring(alertContent)
            parseLoadedAlert(alert, finalAlerts, activeAlerts)

# Run the hourly alert reader for the entire day, for water and land alerts
def readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts):
    
    session = req.Session()
    while(currentHour <= endHour):
        # Do Something
        print(f'Working on --- Date: {currentHour.year}{currentHour.month:02d}{currentHour.day:02d} Hour: {currentHour.hour:02d}')
        datetimeString = f"{currentHour.year}{currentHour.month:02d}{currentHour.day:02d}"
        readHourAlerts(datetimeString, f"{currentHour.hour:02d}", "LAND", finalAlerts, activeAlerts, session)
        readHourAlerts(datetimeString, f"{currentHour.hour:02d}", "WATR", finalAlerts, activeAlerts, session)

        currentHour = currentHour + timedelta(hours=1)


def main():
    currentHour = datetime.strptime(f"{input('Input the starting date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    endHour = datetime.strptime(f"{input('Input the ending date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    finalAlerts = []
    activeAlerts = []
    readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts)
    print("\n\nFinal Alerts:")
    for alert in finalAlerts:
        print(f"Location: {alert.location}, Start Time: {alert.startTime}, End Time: {alert.endTime}")    
    print("\n\nActive Alerts:")
    for alert in activeAlerts:
        print(f"Location: {alert.location}, Start Time: {alert.startTime}, Expiry Time: {alert.expiryTime}")
    
if __name__ == "__main__":
    main()