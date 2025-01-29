import PySimpleGUI as sg
import re

# ********************************** PySimpleGUI - Collect User Info **********************************

# Define the first window's layout
def create_first_window():
    layout = [
        [sg.Text("What's your first name?")],
        [sg.Input(key="first_name")],
        [sg.Text("What's your last name?")],
        [sg.Input(key="last_name")],
        [sg.Button('Next')]
    ]
    return sg.Window('User Info', layout)

# Define the second window for social links
def next_socials_window():
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

# Define the third window for entering scrape URLs
def collect_scrape_urls_window():
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
                    url = "https://" + url  # Ensure URLs have the correct format
                scrape_urls.append(url)
                window["url_list"].update(scrape_urls)
                window["url_input"].update("")  # Clear input box
            else:
                sg.popup_error("Invalid URL! Please enter a valid URL (e.g., https://example.com)")

    window.close()
    return scrape_urls

# Define the final window to display collected data
def display_results_window(first_name, last_name, linkedin_url, personal_website, github_url, scrape_urls):
    urls_text = "\n".join(scrape_urls) if scrape_urls else "No URLs entered"
    layout = [
        [sg.Text(f"Name: {first_name} {last_name}", font=("Helvetica", 14))],
        [sg.Text(f"LinkedIn: {linkedin_url if linkedin_url else 'Not Provided'}")],
        [sg.Text(f"Website: {personal_website if personal_website else 'Not Provided'}")],
        [sg.Text(f"GitHub: {github_url if github_url else 'Not Provided'}")],
        [sg.Text("URLs to Scrape:")],
        [sg.Multiline(urls_text, size=(50, 10), disabled=True)],
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
    print("Window closed. Exiting...")
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
    print("Window closed. Exiting...")
    exit()

linkedin_url = values.get("linkedin_url", "").strip()
personal_website = values.get("personal_website", "").strip()
github_url = values.get("github_url", "").strip()

# ********************** Collect Scrape URLs (Third Window) **********************
scrape_urls = collect_scrape_urls_window()

# ********************** Display Final Data in a New Window **********************
display_results_window(first_name, last_name, linkedin_url, personal_website, github_url, scrape_urls)
