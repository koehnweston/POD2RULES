# app.py
import streamlit as st
import pandas as pd
import requests
import datetime
import re
import os
from collections import defaultdict
import toml
from streamlit_gsheets import GSheetsConnection

# --- Page and App Configuration ---

st.set_page_config(
    page_title="CFB Weekly Picks",
    page_icon="🏈",
    layout="wide",
    initial_sidebar_state="auto"
)

# --- Constants and User Data ---
USERS = {
    "Paul": "pass123", "Weston": "pass123", "Jared": "pass123",
    "Cole": "pass123", "Andy": "pass123", "Krystal": "pass123",
    "Rian": "pass123", "Tucker": "pass123", "Aaron": "pass123",
    "Brayson": "pass123"
}

# --- Helper Functions (with Caching) ---

@st.cache_data
def parse_draft_summary(file_path="draft_summary.txt"):
    """Parses the draft summary text file into a dictionary (for sidebar display)."""
    if not os.path.exists(file_path):
        return {}
    all_picks = {}
    current_user = None
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if line.startswith("---") and line.endswith("---"):
                user_name = line.replace("---", "").replace("'s Picks", "").strip()
                current_user = user_name
                all_picks[current_user] = []
            elif current_user and line and (line and line[0].isdigit()):
                team_name = ''.join([i for i in line if not i.isdigit()]).lstrip('. ')
                all_picks[current_user].append(team_name)
    return all_picks

def get_current_week():
    """Calculates the current week of the season."""
    # Season start date is set to Friday, August 22, 2025. Week 1 games start the next day.
    # The week calculation starts relative to the Monday of that week.
    season_start_date = datetime.date(2025, 8, 18) 
    today = datetime.date.today()
    days_since_start = (today - season_start_date).days
    # Week 1 starts at day 0.
    current_week = (days_since_start // 7) + 1 if days_since_start >= 0 else 1
    return min(current_week, 15)

# --- API & Data Fetching Functions ---

def fetch_api_data(endpoint, params):
    """Generic function to fetch data from the collegefootballdata API."""
    if "secrets" not in st.secrets or "api_key" not in st.secrets.secrets:
        st.error("API key not found. Please add it to your Streamlit app settings.")
        return None, "API key not configured."

    auth_header_value = f"Bearer {st.secrets.secrets.api_key}"
    headers = {'accept': 'application/json', 'Authorization': auth_header_value}
    
    try:
        response = requests.get(f"https://api.collegefootballdata.com/{endpoint}", headers=headers, params=params)
        response.raise_for_status()
        return response.json(), None
    except requests.exceptions.HTTPError as e:
        return None, f"API request failed: {e.response.status_code} - {e.response.text}."
    except requests.exceptions.RequestException as e:
        return None, f"Connection Error: {e}"

@st.cache_data(ttl=3600)
def fetch_game_results(year, week):
    """Fetches game results for a given week and returns a set of winning teams."""
    games_data, error = fetch_api_data("games", {'year': year, 'week': week, 'seasonType': 'regular'})
    if error:
        st.error(error)
        return set()
    if not games_data:
        st.warning(f"No game data found for Week {week}.")
        return set()
    winning_teams = set()
    for game in games_data:
        if game.get('home_points') is not None and game.get('away_points') is not None:
            if game['home_points'] > game['away_points']:
                winning_teams.add(game['home_team'])
            elif game['away_points'] > game['home_points']:
                winning_teams.add(game['away_team'])
    return winning_teams

# --- Scoreboard Logic (with Google Sheets) ---

def update_scoreboard(week, year):
    """Calculates scores for a week and updates the Google Sheet."""
    conn = st.connection("gsheets", type=GSheetsConnection)
    
    with st.spinner(f"Fetching winners and calculating scores for Week {week}..."):
        winning_teams = fetch_game_results(year, week)
        if not winning_teams:
            return

        all_picks_df = conn.read(worksheet="Picks")
        week_picks_df = all_picks_df[all_picks_df["Week"] == week]
        
        if week_picks_df.empty:
            st.warning(f"No user picks found in the database for Week {week}.")
            return

        scores = {}
        for user in week_picks_df["User"].unique():
            user_picks = week_picks_df[week_picks_df["User"] == user]["Team"].tolist()
            wins = sum(1 for team in user_picks if team in winning_teams)
            scores[user] = wins
        
        new_scores_df = pd.DataFrame({
            "User": scores.keys(),
            "Week": week,
            "Wins": scores.values()
        })

        scoreboard_df = conn.read(worksheet="Scoreboard")
        # Remove any existing scores for this week before adding the new ones
        scoreboard_df = scoreboard_df[scoreboard_df["Week"] != week]
        updated_scoreboard = pd.concat([scoreboard_df, new_scores_df], ignore_index=True)
        
        conn.update(worksheet="Scoreboard", data=updated_scoreboard)
        
        st.success(f"Scoreboard successfully updated for Week {week}!")
        # Clear cache to ensure scoreboard reflects the latest update
        st.cache_data.clear()

def display_scoreboard():
    """Loads scoreboard data from Google Sheets and displays it."""
    st.header("🏆 Overall Standings")
    try:
        conn = st.connection("gsheets", type=GSheetsConnection)
        df = conn.read(worksheet="Scoreboard", usecols=[0, 1, 2], ttl="10m")
        df.dropna(how="all", inplace=True)

        if df.empty:
            st.info("Scoreboard is empty. Submit picks and update a week's scores to begin.")
            return
        
        pivot_df = df.pivot_table(index='User', columns='Week', values='Wins', aggfunc='sum').fillna(0)
        pivot_df['Total Wins'] = pivot_df.sum(axis=1)
        pivot_df = pivot_df.sort_values(by='Total Wins', ascending=False).astype(int)
        
        st.dataframe(pivot_df, use_container_width=True)
    except Exception as e:
        st.error(f"Could not connect to or read from Google Sheets: {e}")

# --- UI Component Functions ---

def display_login_form():
    """Displays the login form."""
    st.header("🏈 College Football Weekly Picks")
    with st.form("login_form"):
        username = st.text_input("Username", key="login_username")
        password = st.text_input("Password", type="password", key="login_password")
        submitted = st.form_submit_button("Login")
        if submitted:
            if username in USERS and USERS[username] == password:
                st.session_state.logged_in = True
                st.session_state.username = username
                st.rerun()
            else:
                st.error("Invalid username or password.")

# --- Main Application Logic ---

def main_app():
    """The main application interface shown after a successful login."""
    with st.sidebar:
        st.header(f"🏈 Welcome, {st.session_state.username}!")
        st.write("Your Drafted Teams:")
        # Convert list to DataFrame for better display
        my_teams_df = pd.DataFrame(st.session_state.my_teams, columns=["Team"])
        st.dataframe(my_teams_df, hide_index=True, use_container_width=True)
        st.divider()
        st.button("Logout", on_click=lambda: st.session_state.clear(), use_container_width=True)

    tab1, tab2 = st.tabs(["Weekly Picks", "🏆 Scoreboard"])

    with tab1:
        st.title("Weekly Picks Selection")
        current_week = int(st.selectbox(
            "Select Week",
            options=[f"Week {i}" for i in range(1, 16)], # Weeks 1-15
            index=get_current_week() - 1
        ).split(" ")[1])
        current_year = datetime.datetime.now().year

        try:
            schedule_df = pd.read_csv(f"{current_year}_week_{current_week}.csv")
            matchups = {row['homeTeam']: row['awayTeam'] for _, row in schedule_df.iterrows()}
            matchups.update({row['awayTeam']: row['homeTeam'] for _, row in schedule_df.iterrows()})
        except FileNotFoundError:
            st.warning(f"Schedule file '{current_year}_week_{current_week}.csv' not found.")
            matchups = {}

        picks_data = []
        for team in st.session_state.my_teams:
            opponent = matchups.get(team, "BYE WEEK")
            picks_data.append({"Select": False, "My Team": team, "Opponent": opponent})
        picks_df = pd.DataFrame(picks_data)

        st.subheader(f"Your Matchups for Week {current_week}")

        if not picks_df.empty:
            edited_df = st.data_editor(
                picks_df,
                column_config={"Select": st.column_config.CheckboxColumn("Select", default=False)},
                disabled=["My Team", "Opponent"],
                hide_index=True, use_container_width=True, key=f"picks_editor_{current_week}"
            )
            selected_teams = edited_df[edited_df["Select"]]["My Team"].tolist()
            
            if selected_teams:
                st.subheader("Submit Your Picks")
                num_picks = len(selected_teams)
                if current_week > 0 and num_picks != 6:
                    st.warning(f"⚠️ The standard is 6 picks, but you have selected **{num_picks}**.")

                if st.button("✅ Submit My Picks for this Week", use_container_width=True, type="primary"):
                    picks_to_save = pd.DataFrame({
                        "User": [st.session_state.username] * num_picks,
                        "Week": [current_week] * num_picks,
                        "Team": selected_teams
                    })
                    
                    conn = st.connection("gsheets", type=GSheetsConnection)
                    
                    # Read existing picks to avoid duplicates
                    existing_picks_df = conn.read(worksheet="Picks")
                    
                    # *** MODIFIED LOGIC ***
                    # Filter out any pre-existing picks from this user for this week
                    if not existing_picks_df.empty:
                        condition = (existing_picks_df['User'] == st.session_state.username) & (existing_picks_df['Week'] == current_week)
                        existing_picks_df = existing_picks_df[~condition]
                    
                    # Add the new picks
                    updated_picks_df = pd.concat([existing_picks_df, picks_to_save], ignore_index=True)
                    conn.update(worksheet="Picks", data=updated_picks_df)
                    
                    st.success(f"Successfully submitted {num_picks} picks for Week {current_week}!")

    with tab2:
        st.title("League Scoreboard")
        st.subheader("Update Weekly Scores")
        st.markdown("Select a completed week and click the button to update the standings.")
        
        # Determine the maximum week that can be updated (last week)
        max_week = get_current_week()
        updatable_weeks = range(1, max_week) # Can only update weeks that have passed
        
        if not updatable_weeks:
            st.info("No past weeks are available to update yet.")
        else:
            week_to_update = st.selectbox(
                "Select week to update scores",
                options=updatable_weeks,
                index=len(updatable_weeks) - 1,
            )
            
            if st.button(f"Calculate & Update Scores for Week {week_to_update}", type="primary"):
                update_scoreboard(week_to_update, datetime.datetime.now().year)
        
        st.divider()
        display_scoreboard()

# --- App Initialization and State Management ---

if 'logged_in' not in st.session_state:
    st.session_state.logged_in = False

# --- Main Render Logic ---

if st.session_state.logged_in:
    if 'my_teams' not in st.session_state:
        all_picks = parse_draft_summary()
        st.session_state.my_teams = all_picks.get(st.session_state.username, [])
    main_app()
else:
    # *** SYNTAX CORRECTION ***
    display_login_form()
