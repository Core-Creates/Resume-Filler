import os
import re
import time
import csv
import pdfplumber
from datetime import datetime
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.edge.service import Service as EdgeService
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.microsoft import EdgeChromiumDriverManager
from selenium import webdriver
import PySimpleGUI as sg
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# ********************************** Safe Popup **********************************
def safe_popup(message):
    """Ensures pop-ups do not block the script execution."""
    sg.popup_no_wait(message)

# ********************************** Login Function **********************************
def login_to_job_site(driver, login_url, username, password):
    """Logs into the job search site using provided credentials."""
    driver.get(login_url)
    time.sleep(3)  # Allow time for page load

    # Possible login fields across different job sites
    login_fields = [
        {"user": "//input[@id='session_key' or @name='username']", "pass": "//input[@id='session_password' or @name='password']"},
        {"user": "//input[@type='email']", "pass": "//input[@type='password']"},
        {"user": "//input[contains(@name, 'email')]", "pass": "//input[contains(@name, 'password')]"},
    ]

    for fields in login_fields:
        try:
            username_input = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, fields["user"])))
            password_input = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, fields["pass"])))
            login_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, "//button[@type='submit' or contains(text(), 'Sign in')]")))

            username_input.clear()
            password_input.clear()

            username_input.send_keys(username)
            password_input.send_keys(password)
            login_button.click()
            
            time.sleep(5)  # Wait for login attempt
            if "login" not in driver.current_url.lower():
                safe_popup("✅ Login successful!")
                return True
        except:
            continue

    safe_popup("⚠️ Login failed! Check your credentials and try again.")
    return False

# ********************************** Resume Parsing **********************************
def extract_resume_data(resume_path):
    """Extracts key details from the user's resume (Name, Email, Phone, Job History)."""
    user_data = {"first_name": "", "last_name": "", "email": "", "phone": "", "job_history": []}

    try:
        with pdfplumber.open(resume_path) as pdf:
            text = "\n".join(page.extract_text() for page in pdf.pages if page.extract_text())

        # Extract Email
        email_match = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
        user_data["email"] = email_match.group(0) if email_match else ""

        # Extract Phone Number
        phone_match = re.search(r"(\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4})", text)
        user_data["phone"] = phone_match.group(0) if phone_match else ""

        # Extract Name (Handles Multi-Part Names)
        name_lines = text.split("\n")[:5]
        name_match = re.search(r"([A-Z][a-z]+(?: [A-Z][a-z]+)*) ([A-Z][a-z]+)", " ".join(name_lines))
        if name_match:
            user_data["first_name"], user_data["last_name"] = name_match.groups()

        # Extract Job History
        job_matches = re.findall(r"([A-Za-z\s]+),\s([A-Za-z\s]+),\s(\d{4}[-–]\d{4}|\d{4}-Present)", text)
        user_data["job_history"] = [{"company": job[0], "role": job[1], "dates": job[2]} for job in job_matches]

    except Exception as e:
        safe_popup(f"❌ Error reading resume: {e}")

    return user_data

# ********************************** Apply to Job **********************************
def apply_to_job(driver, job_link, user_data, resume_path):
    """Automates the job application process using extracted resume data."""
    try:
        driver.get(job_link)
        time.sleep(3)

        apply_buttons = [
            '//button[contains(text(), "Apply Now")]',
            '//button[contains(text(), "Quick Apply")]',
            '//a[contains(text(), "Apply")]'
        ]

        for xpath in apply_buttons:
            try:
                apply_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                apply_button.click()
                time.sleep(2)
                break
            except:
                continue

        # Submit application
        submit_buttons = [
            '//button[contains(text(), "Submit")]',
            '//button[contains(text(), "Finish")]',
            '//button[contains(text(), "Send Application")]'
        ]

        for xpath in submit_buttons:
            try:
                submit_button = WebDriverWait(driver, 5).until(EC.element_to_be_clickable((By.XPATH, xpath)))
                submit_button.click()
                safe_popup("✅ Application Submitted Successfully!")
                return True
            except:
                continue

        safe_popup("⚠️ Could not submit the application.")
        return False

    except Exception as e:
        safe_popup(f"❌ Failed to apply: {e}")
        return False

# ********************************** Scrape & Apply **********************************
def scrape_and_apply(login_url, job_search_url, desired_jobs, location, resume_path, username, password):
    """Logs in, scrapes jobs, and applies automatically."""
    driver = webdriver.Edge(service=EdgeService(EdgeChromiumDriverManager().install()))

    # Login first
    login_attempts = 3
    for attempt in range(login_attempts):
        if login_to_job_site(driver, login_url, username, password):
            break
        elif attempt == login_attempts - 1:
            safe_popup("❌ Login failed after multiple attempts. Exiting.")
            driver.quit()
            return

    driver.get(job_search_url)
    search_input = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//input[@type='text']")))
    location_input = WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.XPATH, "//input[@name='l' or @id='location']")))
    
    search_input.send_keys(", ".join(desired_jobs))
    location_input.send_keys(location)
    location_input.send_keys(Keys.RETURN)
    
    time.sleep(5)
    job_results = []

    for _ in range(3):
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(3)

    job_elements = driver.find_elements(By.CSS_SELECTOR, ".job_seen_beacon, .job-card-container")

    for job in job_elements:
        try:
            title = job.find_element(By.TAG_NAME, "h2").text
            link = job.find_element(By.TAG_NAME, "a").get_attribute("href")
            job_results.append({"title": title, "link": link})
        except:
            continue

    safe_popup(f"✅ {len(job_results)} jobs found! Starting applications...")

    user_data = extract_resume_data(resume_path)

    for job in job_results:
        apply_to_job(driver, job["link"], user_data, resume_path)

    driver.quit()

# ********************************** Execution **********************************
if __name__ == "__main__":
    safe_popup("Starting automated job application process...")

    username, password = "testuser@example.com", "password123"  # Replace with actual user input

    scrape_and_apply(
        "https://www.linkedin.com/login",
        "https://www.linkedin.com/jobs/",
        ["Software Engineer"],
        "Remote",
        "resume.pdf",
        username,
        password
    )
