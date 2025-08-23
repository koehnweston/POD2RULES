# app.py
import streamlit as st
import pandas as pd
import requests
import datetime
import re
import os
from collections import defaultdict
from sqlalchemy import text

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
    # Set to a date in 2025 for consistent testing
    season_start_date = datetime.date(2025, 8, 18) 
    today = datetime.date.today()
    # If today is before the season start, default to week 1
    if today < season_start_date:
        return 1
    days_since_start = (today - season_start_date).days
    current_week = (days_since_start // 7) + 1
    return min(current_week, 15)

# --- API & Data Fetching Functions ---

def fetch_api_data(endpoint, params):
    """Generic function to fetch data from the collegefootballdata API."""
    if "api_key" not in st.secrets:
        st.error("API key not found. Please add it to your Streamlit app settings.")
        return None, "API key not configured."

    auth_header_value = f"Bearer {st.secrets.api_key}"
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

# --- Scoreboard Logic (with SQL Database) ---

def update_scoreboard(week, year):
    """Calculates scores for a week and updates the database."""
    conn = st.connection("db", type="sql")
    
    with st.spinner(f"Fetching winners and calculating scores for Week {week}..."):
        winning_teams = fetch_game_results(year, week)
        if not winning_teams:
            return

        all_picks_df = conn.query(f"SELECT * FROM picks WHERE week = {week};")
        
        if all_picks_df.empty:
            st.warning(f"No user picks found in the database for Week {week}.")
            return

        scores = {}
        for user in all_picks_df["user"].unique():
            user_picks = all_picks_df[all_picks_df["user"] == user]["team"].tolist()
            wins = sum(1 for team in user_picks if team in winning_teams)
            scores[user] = wins
        
        # Use a session to execute delete and inserts in a transaction
        with conn.session as s:
            # Delete old scores for the week to prevent duplicates
            s.execute(text(f"DELETE FROM scoreboard WHERE week = {week};"))
            
            # Insert new scores
            for user, wins in scores.items():
                s.execute(
                    text('INSERT INTO scoreboard ("user", week, wins) VALUES (:user, :week, :wins);'),
                    params=dict(user=user, week=week, wins=wins)
                )
            s.commit()
            
        st.success(f"Scoreboard successfully updated for Week {week}!")
        st.cache_data.clear()

def display_scoreboard():
    """Loads scoreboard data from the database and displays it."""
    st.header("🏆 Overall Standings")
    try:
        conn = st.connection("db", type="sql")
        df = conn.query("SELECT * FROM scoreboard;")

        if df.empty:
            st.info("Scoreboard is empty. Submit picks and update a week's scores to begin.")
            return
        
        df.rename(columns={'user': 'User', 'week': 'Week', 'wins': 'Wins'}, inplace=True)
        
        pivot_df = df.pivot_table(index='User', columns='Week', values='Wins', aggfunc='sum').fillna(0)
        pivot_df['Total Wins'] = pivot_df.sum(axis=1)
        pivot_df = pivot_df.sort_values(by='Total Wins', ascending=False).astype(int)
        
        st.dataframe(pivot_df, use_container_width=True)
    except Exception as e:
        st.error(f"Could not connect to or read from the database: {e}")

# --- UI Component Functions ---

def display_user_picks(user, week):
    """Fetches and displays a user's picks for a given week from the database."""
    st.subheader(f"Your Submitted Picks for Week {week}")
    
    # Use ttl=0 to disable caching for this specific, time-sensitive query
    conn = st.connection("db", type="sql", ttl=0) 
    
    picks_df = conn.query(
        'SELECT team FROM picks WHERE "user" = :user AND week = :week;',
        params={"user": user, "week": week}
    )
    
    if picks_df.empty:
        st.info("You have not submitted any picks for this week yet.")
    else:
        picks_df.rename(columns={'team': 'Selected Team'}, inplace=True)
        st.dataframe(picks_df, hide_index=True, use_container_width=True)

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
        my_teams_df = pd.DataFrame(st.session_state.my_teams, columns=["Team"])
        st.dataframe(my_teams_df, hide_index=True, use_container_width=True)
        st.divider()
        if st.button("Logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    # Add a dedicated "View My Picks" tab for clarity
    tab1, tab2, tab3 = st.tabs(["✍️ Make Picks", "👀 View My Picks", "🏆 Scoreboard"])

    # --- TAB 1: MAKE PICKS ---
    with tab1:
        st.title("Weekly Picks Selection")
        current_week = int(st.selectbox(
            "Select Week",
            options=[f"Week {i}" for i in range(1, 16)],
            index=get_current_week() - 1,
            key="week_selector_tab1"
        ).split(" ")[1])
        current_year = datetime.datetime.now().year

        # Initialize a version counter to force the editor to reset
        if 'editor_version' not in st.session_state:
            st.session_state.editor_version = 0

        # Always query the database for the true state of the picks
        conn = st.connection("db", type="sql", ttl=0)
        existing_picks_df = conn.query(
            'SELECT team FROM picks WHERE "user" = :user AND week = :week;',
            params={"user": st.session_state.username, "week": current_week}
        )
        existing_picks = set(existing_picks_df['team'])

        # Load schedule data
        try:
            schedule_df = pd.read_csv(f"{current_year}_week_{current_week}.csv")
            matchups = {row['homeTeam']: row['awayTeam'] for _, row in schedule_df.iterrows()}
            matchups.update({row['awayTeam']: row['homeTeam'] for _, row in schedule_df.iterrows()})
        except FileNotFoundError:
            st.warning(f"Schedule file '{current_year}_week_{current_week}.csv' not found.")
            matchups = {}

        # The editor's DataFrame is always built fresh from the database query
        picks_data = []
        for team in st.session_state.my_teams:
            opponent = matchups.get(team, "BYE WEEK")
            is_selected = team in existing_picks
            picks_data.append({"Select": is_selected, "My Team": team, "Opponent": opponent})
        picks_df = pd.DataFrame(picks_data)

        st.subheader(f"Your Matchups for Week {current_week}")
        edited_df = st.data_editor(
            picks_df,
            column_config={"Select": st.column_config.CheckboxColumn("Select", default=False)},
            disabled=["My Team", "Opponent"],
            hide_index=True,
            use_container_width=True,
            # CRITICAL: The key changes after every action, forcing a reset
            key=f"picks_editor_{current_week}_{st.session_state.editor_version}"
        )

        selected_teams = edited_df[edited_df["Select"]]["My Team"].tolist()

        col1, col2 = st.columns(2)
        with col1:
            if st.button("✅ Submit Picks", use_container_width=True, type="primary"):
                with st.connection("db", type="sql").session as s:
                    s.execute(text('DELETE FROM picks WHERE "user" = :user AND week = :week;'), params={"user": st.session_state.username, "week": current_week})
                    for team in selected_teams:
                        s.execute(text('INSERT INTO picks ("user", week, team) VALUES (:user, :week, :team);'), params={"user": st.session_state.username, "week": current_week, "team": team})
                    s.commit()
                st.success("Picks submitted! Check the 'View My Picks' tab to confirm.")
                # Increment the key version to force a widget reset
                st.session_state.editor_version += 1
                st.rerun()

        with col2:
            if st.button("❌ Clear Picks", use_container_width=True):
                with st.connection("db", type="sql").session as s:
                    s.execute(text('DELETE FROM picks WHERE "user" = :user AND week = :week;'), params={"user": st.session_state.username, "week": current_week})
                    s.commit()
                st.success("Picks cleared successfully!")
                # Increment the key version to force a widget reset
                st.session_state.editor_version += 1
                st.rerun()

    # --- TAB 2: VIEW MY PICKS ---
    with tab2:
        st.title("Your Submitted Picks")
        view_week = int(st.selectbox(
            "Select Week to View",
            options=[f"Week {i}" for i in range(1, 16)],
            index=get_current_week() - 1,
            key="week_selector_tab2"
        ).split(" ")[1])
        st.divider()
        # This function provides a clean, read-only view of the database
        display_user_picks(st.session_state.username, view_week)

    # --- TAB 3: SCOREBOARD ---
    with tab3:
        st.title("League Scoreboard")
        st.subheader("Update Weekly Scores")
        st.markdown("Select a completed week and click the button to update the standings.")
        
        max_week = get_current_week()
        updatable_weeks = range(1, max_week)
        
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
    display_login_form()
