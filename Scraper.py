import csv
import os
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
from selenium import webdriver
import PySimpleGUI as sg
import re

# ********************************** CSV Export Function **********************************
def save_jobs_to_csv(jobs, location):
    """Saves job listings to a CSV file with a timestamp."""
    if not jobs:
        sg.popup("No jobs available to save.")
        return
    
    # Define CSV file path
    filename = "job_listings.csv"
    file_exists = os.path.isfile(filename)
    
    # Write to CSV
    with open(filename, mode="a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        
        # Write headers only if file is new
        if not file_exists:
            writer.writerow(["Job Title", "Job URL", "Location", "Date Scraped"])
        
        # Write job listings
        for job in jobs:
            writer.writerow([job["title"], job["link"], location, datetime.now().strftime("%Y-%m-%d %H:%M:%S")])
    
    sg.popup(f"✅ Jobs saved successfully to {filename}!")


# ********************************** Web Scraping Function **********************************
def scrape_search_selenium(url, desired_jobs, location):
    """Scrapes job listings dynamically from a job search website."""
    
    try:
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()))
    except Exception as e:
        sg.popup_error(f"Error initializing WebDriver: {e}")
        return []

    try:
        driver.get(url)
        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.TAG_NAME, "body")))

        # Handle different job sites (LinkedIn, Indeed, etc.)
        search_fields = [
            ('//input[contains(@class, "jobs-search-box__text-input")]', '//input[contains(@class, "jobs-search-box__text-input")][2]'),  # LinkedIn
            ('//input[@id="text-input-what"]', '//input[@id="text-input-where"]')  # Indeed
        ]

        search_input, search_location = None, None
        for search_xpath, location_xpath in search_fields:
            try:
                search_input = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, search_xpath)))
                search_location = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, location_xpath)))
                break  
            except:
                continue

        if not search_input or not search_location:
            sg.popup_error("Error: Unable to locate search fields. The website structure may have changed.")
            driver.quit()
            return []

        search_query = ", ".join(desired_jobs)
        search_input.send_keys(search_query)
        search_location.clear()
        search_location.send_keys(location)
        search_location.send_keys(Keys.RETURN)

        WebDriverWait(driver, 10).until(EC.presence_of_element_located((By.CSS_SELECTOR, ".job-card-container, .job_seen_beacon")))

        # Extract job listings
        desired_job_results = []
        job_elements = driver.find_elements(By.CSS_SELECTOR, ".job-card-container, .job_seen_beacon")

        if not job_elements:
            sg.popup("No job listings found. Try a different search term or check the website.")
            driver.quit()
            return []

        for job in job_elements:
            try:
                title = job.find_element(By.CSS_SELECTOR, "h2, .job-title").text
                link = job.find_element(By.CSS_SELECTOR, "a").get_attribute("href")
                desired_job_results.append({"title": title, "link": link})
            except Exception as e:
                print(f"Error extracting job details: {e}")
                continue  

        driver.quit()
        return desired_job_results  

    except Exception as e:
        sg.popup_error(f"Scraping Error: {str(e)}")
        driver.quit()
        return []


# ********************************** PySimpleGUI Windows **********************************
def display_results_window(first_name, last_name, linkedin_url, personal_website, github_url, job_experiences, scrape_urls, scraped_data, jobs, location):
    """Displays results including job search results and allows saving to CSV."""
    job_text = "\n\n".join([f"💼 {title}\n  {location}\n {desc}" for title, location, desc in job_experiences]) if job_experiences else "No job experience entered"
    urls_text = "\n".join([f"✅ {url}" for url in scrape_urls]) if scrape_urls else "No URLs entered"
    scrape_results_text = "\n\n".join([f"🔗 {url}\n📌 Title: {data.get('title', 'N/A')}\n🔗 Links Found: {len(data.get('links', []))}" for url, data in scraped_data.items()]) if scraped_data else "No scraping results."
    job_results_text = "\n\n".join([f"🔍 {job['title']}\n🔗 {job['link']}" for job in jobs]) if jobs else "No jobs found."

    layout = [
        [sg.Text(f"👤 Name: {first_name} {last_name}", font=("Helvetica", 14))],
        [sg.Text(f"🔗 LinkedIn: {linkedin_url if linkedin_url else 'Not Provided'}")],
        [sg.Text(f"🌐 Website: {personal_website if personal_website else 'Not Provided'}")],
        [sg.Text(f"🐙 GitHub: {github_url if github_url else 'Not Provided'}")],
        [sg.Text("\n💼 Work Experience:")],
        [sg.Multiline(job_text, size=(50, 10), disabled=True)],
        [sg.Text("\n🌍 URLs to Scrape:")],
        [sg.Multiline(urls_text, size=(50, 10), disabled=True)],
        [sg.Text("\n🔍 Scraping Results:")],
        [sg.Multiline(scrape_results_text, size=(50, 10), disabled=True)],
        [sg.Text("\n📢 Job Search Results:")],
        [sg.Multiline(job_results_text, size=(50, 10), disabled=True)],  # Display job results
        [sg.Button('Save to CSV'), sg.Button('Submit Resume'), sg.Button('Close')]
    ]
    
    window = sg.Window('Collected Information', layout)
    
    while True:
        event, _ = window.read()
        if event in (sg.WIN_CLOSED, "Close"):
            break
        if event == "Save to CSV":
            save_jobs_to_csv(jobs, location)  # Save to CSV when button is clicked

    window.close()


# ********************************** Execution Flow **********************************
# Ask user for job search URL
job_search_url = sg.popup_get_text("Enter the job search website URL:", default_text="https://www.linkedin.com/jobs/")

# Validate URL
url_pattern = re.compile(r'^(https?://)?(www\.)?([a-zA-Z0-9-]+)\.(com|org|net|edu|gov)(/.*)?$', re.IGNORECASE)
if not job_search_url or not url_pattern.match(job_search_url):
    sg.popup_error("Invalid URL! Please enter a valid job search website URL.")
    exit()

# Ask user for job location
location = sg.popup_get_text("Enter job location:", default_text="Remote") or "Remote"

# Ensure user enters at least one job title before scraping
if not desired_jobs:
    sg.popup_error("You must enter at least one job title to search.")
    exit()

# Call `scrape_search_selenium()`
jobs = scrape_search_selenium(job_search_url, desired_jobs, location)

# Show popup if no jobs are found
if not jobs:
    sg.popup("No jobs found for the given search criteria.")

# Final Display
display_results_window(first_name, last_name, linkedin_url, personal_website, github_url, job_experiences, scrape_urls, scraped_data, jobs, location)
