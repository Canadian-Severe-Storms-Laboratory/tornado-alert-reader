import re
import requests as req
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import csv

class Alert:

    def __init__(self, location, startTime, expiryTime, province):
        self.location = location
        self.startTime = startTime
        self.endTime = None
        self.expiryTime = expiryTime
        self.province = province

    location: str
    province: str
    startTime: datetime
    endTime: datetime
    expiryTime: datetime


def searchAlertList(alertList, location) -> int:
    for i in range(len(alertList)):
        if(alertList[i].location == location):
            return i
    return -1

# Return true if the warning is a tornado warning, false otherwise
def parseLoadedAlert(alert, finalAlerts, activeAlerts, prov):
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

                    # Check for cases where the alert is cancelled --- could be actually getting cancelled (i.e., the alert is on the active list)
                    # Could also be a case where the alert is "cancelled" but is not on the active list (a so-called "second cancellation")
                    if(index != -1):
                        if(currentEffectiveTime == currentExpiryTime or elm.find("cap:responseType", ns).text == "AllClear"):
                            activeAlerts[index].endTime = currentEffectiveTime
                            finalAlerts.append(activeAlerts.pop(index))
                    else:
                        if(currentEffectiveTime != currentExpiryTime and elm.find("cap:responseType", ns).text != "AllClear"):

                            # Handle great lakes specific tag, which is always Ontario
                            if(prov == "GL"):
                                prov = "ON"
                            activeAlerts.append(Alert(currentLocation, currentEffectiveTime, currentExpiryTime, prov))
                 

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
            # Grab provinical code from the link
            prov = link[2:4]

            alertContent = session.get(f'{URL}{link}', timeout=10).content
            alert = ET.fromstring(alertContent)
            parseLoadedAlert(alert, finalAlerts, activeAlerts, prov)

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

    # Once all alerts have been read, check if any of the "active" alerts have actually passed their expiry time
    for alert in activeAlerts:
        if(alert.expiryTime < endHour +timedelta(hours=1)):
            alert.endTime = alert.expiryTime
            finalAlerts.append(alert)




def main():
    currentHour = datetime.strptime(f"{input('Input the starting date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    endHour = datetime.strptime(f"{input('Input the ending date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    finalAlerts = []
    activeAlerts = []
    readAlertsForRange(currentHour, endHour, finalAlerts, activeAlerts)


    finalAlerts.sort(key=lambda x: x.startTime)
    activeAlerts.sort(key=lambda x: x.startTime)

    print("\n\nFinal Alerts:")
    for alert in finalAlerts:
        print(f"Location: {alert.location}, Start Time: {alert.startTime}, End Time: {alert.endTime}, Province: {alert.province}")    
    print("\n\nActive Alerts:")
    for alert in activeAlerts:
        print(f"Location: {alert.location}, Start Time: {alert.startTime}, Expiry Time: {alert.expiryTime}, Province: {alert.province}")

if __name__ == "__main__":
    main()