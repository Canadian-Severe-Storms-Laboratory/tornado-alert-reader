import re
import requests as req
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

def readHourAlerts(date, hour, designation, alerts, session):
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
            alerts.append(alert)

# Run the hourly alert reader for the entire day, for water and land alerts
def readAlertsForRange(currentHour, endHour):
    alerts = []
    session = req.Session()
    while(currentHour <= endHour):
        # Do Something
        print(f'Working on --- Date: {currentHour.year}{currentHour.month:02d}{currentHour.day:02d} Hour: {currentHour.hour:02d}')
        datetimeString = f"{currentHour.year}{currentHour.month:02d}{currentHour.day:02d}"
        readHourAlerts(datetimeString, f"{currentHour.hour:02d}", "LAND", alerts, session)
        readHourAlerts(datetimeString, f"{currentHour.hour:02d}", "WATR", alerts, session)

        currentHour = currentHour + timedelta(hours=1)


def main():
    currentHour = datetime.strptime(f"{input('Input the starting date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    endHour = datetime.strptime(f"{input('Input the ending date to read alerts from (YYYY/MM/DD/HH): ').strip()}", "%Y/%m/%d/%H")
    readAlertsForRange(currentHour, endHour)

    

if __name__ == "__main__":
    main()