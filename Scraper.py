import PySimpleGUI as sg
import re

# ********************************** PySimpleGUI - Collect User Info **********************************

def create_first_window():
    """ Collect First & Last Name """
    layout = [
        [sg.Text("What's your first name?")],
        [sg.Input(key="first_name")],
        [sg.Text("What's your last name?")],
        [sg.Input(key="last_name")],
        [sg.Button('Next')]
    ]
    return sg.Window('User Info', layout)

def next_socials_window():
    """ Collect Social Media & Personal Links """
    layout = [
        [sg.Text("Enter your LinkedIn URL:")],
        [sg.Input(key="linkedin_url")],
        [sg.Text("Enter your personal website:")],
        [sg.Input(key="personal_website")],
        [sg.Text("Enter your GitHub:")],
        [sg.Input(key="github_url")],
        [sg.Button('Next')]
    ]
    return sg.Window('Social Links', layout)

def work_experience_window():
    """ Collect Work Experience with a Dynamic 'Add Job' Feature """
    job_experiences = []

    layout = [
        [sg.Text("Enter your work experience")],
        [sg.Text("Job Title:"), sg.Input(key="job_title_1")],
        [sg.Text("Job Description:"), sg.Multiline(size=(50, 5), key="job_description_1")],
        [sg.Button("➕ Add Job"), sg.Button("Submit")]
    ]

    window = sg.Window("Work Experience", layout)
    job_count = 1  # Track job fields dynamically

    while True:
        event, values = window.read()

        if event in (sg.WIN_CLOSED, "Submit"):
            break

        if event == "➕ Add Job":
            job_count += 1
            window.extend_layout(window, [
                [sg.Text(f"Job Title {job_count}:"), sg.Input(key=f"job_title_{job_count}")],
                [sg.Text(f"Job Description {job_count}:"), sg.Multiline(size=(50, 5), key=f"job_description_{job_count}")],
            ])

    for i in range(1, job_count + 1):
        title = values.get(f"job_title_{i}", "").strip()
        description = values.get(f"job_description_{i}", "").strip()
        if title and description:
            job_experiences.append((title, description))

    window.close()
    return job_experiences

def collect_scrape_urls_window():
    """ Collect List of URLs Dynamically """
    scrape_urls = []
    layout = [
        [sg.Text("Enter URLs to scrape:")],
        [sg.Input(key="url_input")],
        [sg.Button("Add URL"), sg.Button("Submit")],
        [sg.Listbox(values=scrape_urls, size=(50, 10), key="url_list", enable_events=True)]
    ]

    window = sg.Window("URL Collection", layout)

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, "Submit"):
            break
        if event == "Add URL":
            url = values["url_input"].strip()
            if url and re.match(r'^(https?://)?(www\.)?([a-zA-Z0-9-]+)(\.[a-zA-Z]+)+(/.*)?$', url):
                if not url.startswith("http"):
                    url = "https://" + url
                scrape_urls.append(url)
                window["url_list"].update(scrape_urls)
                window["url_input"].update("")
            else:
                sg.popup_error("Invalid URL! Please enter a valid URL (e.g., https://example.com)")

    window.close()
    return scrape_urls

def display_results_window(first_name, last_name, linkedin_url, personal_website, github_url, job_experiences, scrape_urls):
    """ Display All Collected Data in a Final Summary Window """
    job_text = "\n\n".join([f"💼 {title}\n   {desc}" for title, desc in job_experiences]) if job_experiences else "No job experience entered"
    urls_text = "\n".join([f"✅ {url}" for url in scrape_urls]) if scrape_urls else "No URLs entered"

    layout = [
        [sg.Text(f"👤 Name: {first_name} {last_name}", font=("Helvetica", 14))],
        [sg.Text("\n📌 Collected Social Links:")],
        [sg.Text(f"🔗 LinkedIn: {linkedin_url if linkedin_url else 'Not Provided'}")],
        [sg.Text(f"🌐 Website: {personal_website if personal_website else 'Not Provided'}")],
        [sg.Text(f"🐙 GitHub: {github_url if github_url else 'Not Provided'}")],
        [sg.Text("\n💼 Work Experience:")],
        [sg.Multiline(job_text, size=(50, 10), disabled=True)],
        [sg.Text("\n🌍 URLs to Scrape:")],
        [sg.Multiline(urls_text, size=(50, 10), disabled=True)],
        [sg.Button('submit resume')],
        [sg.Button('Close')]
    ]
    
    window = sg.Window('Collected Information', layout)
    event, _ = window.read()
    window.close()

# ********************** Collect User Info (First Window) **********************
window = create_first_window()
event, values = window.read()
window.close()

if event in (sg.WIN_CLOSED, None):
    exit()

first_name = values.get("first_name", "").strip()
last_name = values.get("last_name", "").strip()

if not first_name or not last_name:
    sg.popup_error("Error: First and last names cannot be empty.")
    exit()

# ********************** Collect Social Links (Second Window) **********************
window = next_socials_window()
event, values = window.read()
window.close()

if event in (sg.WIN_CLOSED, None):
    exit()

linkedin_url = values.get("linkedin_url", "").strip()
personal_website = values.get("personal_website", "").strip()
github_url = values.get("github_url", "").strip()

# ********************** Work Experience (Third Window) ***************************
job_experiences = work_experience_window()

# ********************** Collect Scrape URLs (Fourth Window) **********************
scrape_urls = collect_scrape_urls_window()

# ********************** Display Final Data in a New Window ***********************
display_results_window(first_name, last_name, linkedin_url, personal_website, github_url, job_experiences, scrape_urls)

# ********************** Scrap the URLs for Job listings ***************************

