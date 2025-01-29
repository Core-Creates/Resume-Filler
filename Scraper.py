# ************************************************************************************************
import PySimpleGUI as sg  # Corrected import
import requests
# ************************************************************************************************

# Initialize user input variables
firstName = ""
lastName = ""

# Define the window's contents
layout = [
    [sg.Text("What's your first name?")],
    [sg.Input(key="first_name")],  # Added key for better handling
    [sg.Text("What's your last name?")],  # Fixed missing text formatting
    [sg.Input(key="last_name")],  # Added input field for last name
    [sg.Button('Ok')]
]

# Create the window
window = sg.Window('User Info', layout)

# Display and interact with the Window
event, values = window.read()

# Check if user closed the window
if event == sg.WIN_CLOSED:
    print("Window closed. Exiting...")
else:
    firstName = values["first_name"]
    lastName = values["last_name"]
    print(f'Hello {firstName} {lastName}! Thanks for trying PySimpleGUI.')

# Close the window
window.close()

# Initialize an empty list to store URLs
scrape_urls = []

print("\nEnter URLs to add to the scraping list. Type 'done' when finished.")

# Loop for user input of URLs
while True:
    user_input = input("Enter a URL: ").strip()

    # Check if the user wants to stop input
    if user_input.lower() == "done":
        break

    # Check if the input is a valid URL
    if user_input.startswith("http://") or user_input.startswith("https://"):
        scrape_urls.append(user_input)
        print(f"Added: {user_input}")
    else:
        print("Invalid URL. Please enter a valid URL (starting with http:// or https://).")

# Display final list of URLs
print("\nFinal list of URLs to scrape:")
for url in scrape_urls:
    print(url)
