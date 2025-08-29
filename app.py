import streamlit as st
import pandas as pd
import requests
import datetime
import re
import os
import pytz # Added for timezone handling
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
    season_start_date = datetime.date(2025, 8, 27)
    today = datetime.date.today()
    if today < season_start_date:
        return 1
    days_since_start = (today - season_start_date).days
    current_week = (days_since_start // 7) + 1
    return min(current_week, 15)

# --- NEW: Function to check if picks are locked for the week ---
def are_picks_locked(week, year):
    """
    Checks if the current time is past the pick deadline for a given week.
    The deadline is 10:59 AM Central Time on the Saturday of that week.
    """
    try:
        central_tz = pytz.timezone("America/Chicago")

        # The season starts on a Wednesday. Find the first Saturday.
        season_start_date = datetime.date(year, 8, 27) 
        # weekday() -> Monday is 0 and Sunday is 6. Saturday is 5.
        days_until_saturday = (5 - season_start_date.weekday() + 7) % 7
        first_saturday = season_start_date + datetime.timedelta(days=days_until_saturday)
        
        # Calculate the target Saturday for the given week
        target_saturday = first_saturday + datetime.timedelta(weeks=week - 1)

        # Create the deadline datetime object (timezone-aware)
        lock_time = datetime.time(10, 59)
        lock_datetime_naive = datetime.datetime.combine(target_saturday, lock_time)
        lock_datetime_aware = central_tz.localize(lock_datetime_naive)

        # Get the current time (timezone-aware)
        now_aware = datetime.datetime.now(central_tz)

        # Return True if the current time is past the deadline
        return now_aware >= lock_datetime_aware
    except Exception as e:
        st.error(f"Error checking lock time: {e}")
        return False # Fail safe: if time check fails, don't lock picks

# --- API & Data Fetching Functions ---

def fetch_api_data(endpoint, params):
    """Generic function to fetch data from the collegefootballdata API."""
    try:
        api_key = st.secrets.api_key
        if not api_key:
            st.error("API key is present but has no value. Please check your Streamlit app settings.")
            return None, "API key is empty."
    except AttributeError:
        st.error("API key not found. Please add it to your Streamlit app settings.")
        return None, "API key not configured."

    auth_header_value = f"Bearer {api_key}"
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

@st.cache_data(ttl=3600)
def fetch_betting_lines(year, week):
    """Fetches betting lines for a given week from the API."""
    lines_data, error = fetch_api_data("lines", {'year': year, 'week': week, 'seasonType': 'regular'})
    if error:
        return {}
    if not lines_data:
        return {}
    betting_lines = {}
    for game in lines_data:
        line_to_use = None
        if game.get('lines'):
            consensus_lines = [line for line in game['lines'] if line.get('provider') == 'consensus']
            if consensus_lines:
                line_to_use = consensus_lines[0]
            else:
                line_to_use = game['lines'][0]
        if line_to_use and line_to_use.get('spread'):
            home_team = game['homeTeam']
            away_team = game['awayTeam']
            spread = float(line_to_use['spread'])
            betting_lines[home_team] = spread
            betting_lines[away_team] = -spread
    return betting_lines

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
        with conn.session as s:
            s.execute(text(f"DELETE FROM scoreboard WHERE week = {week};"))
            for user, wins in scores.items():
                s.execute(
                    text('INSERT INTO scoreboard ("user", week, wins) VALUES (:user, :week, :wins);'),
                    params=dict(user=user, week=week, wins=wins)
                )
            s.commit()
        st.success(f"Scoreboard successfully updated for Week {week}!")
        st.cache_data.clear()
        st.cache_resource.clear()

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
    conn = st.connection("db", type="sql")
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

    tab1, tab2 = st.tabs(["✍️ Weekly Picks", "🏆 Scoreboard"])

    with tab1:
        st.title("Weekly Picks Selection")
        current_week = int(st.selectbox(
            "Select Week",
            options=[f"Week {i}" for i in range(1, 16)],
            index=get_current_week() - 1,
        ).split(" ")[1])
        current_year = datetime.datetime.now().year

        # --- MODIFIED: Check if picks are locked and render UI accordingly ---
        picks_are_locked = are_picks_locked(current_week, current_year)

        if picks_are_locked:
            st.warning(f"🔒 Picks for Week {current_week} are now locked.")
            st.divider()
            display_user_picks(st.session_state.username, current_week)
        else:
            # --- This is the original UI for when picks are NOT locked ---
            with st.spinner(f"Fetching betting lines for Week {current_week}..."):
                betting_lines = fetch_betting_lines(current_year, current_week)

            conn = st.connection("db", type="sql")
            existing_picks_df = conn.query(
                'SELECT team FROM picks WHERE "user" = :user AND week = :week;',
                params={"user": st.session_state.username, "week": current_week}
            )
            existing_picks = set(existing_picks_df['team'])
            
            game_info = {}
            try:
                schedule_df = pd.read_csv(f"{current_year}_week_{current_week}.csv")
                for _, row in schedule_df.iterrows():
                    home_team = row['homeTeam']
                    away_team = row['awayTeam']
                    game_info[home_team] = {'opponent': away_team, 'location': 'Home'}
                    game_info[away_team] = {'opponent': home_team, 'location': 'Away'}
            except FileNotFoundError:
                st.warning(f"Schedule file '{current_year}_week_{current_week}.csv' not found.")
            
            picks_data = []
            for team in st.session_state.my_teams:
                is_selected = team in existing_picks
                match_details = game_info.get(team)
                if match_details:
                    opponent = match_details['opponent']
                    location = match_details['location']
                else:
                    opponent = "BYE WEEK"
                    location = "N/A"
                line = betting_lines.get(team)
                if line is not None:
                    formatted_line = f"+{line}" if line > 0 else str(line)
                else:
                    formatted_line = "N/A"
                picks_data.append({
                    "Select": is_selected, "My Team": team, "Location": location,
                    "Opponent": opponent, "Line": formatted_line
                })
            
            if picks_data:
                picks_df = pd.DataFrame(picks_data)[['Select', 'My Team', 'Location', 'Opponent', 'Line']]
            else:
                picks_df = pd.DataFrame(picks_data)

            st.subheader(f"Your Matchups for Week {current_week}")
            if not picks_df.empty:
                edited_df = st.data_editor(
                    picks_df,
                    column_config={"Select": st.column_config.CheckboxColumn("Select", default=False)},
                    disabled=["My Team", "Location", "Opponent", "Line"],
                    hide_index=True,
                    use_container_width=True,
                    key=f"picks_editor_{current_week}"
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
                        st.success("Picks submitted successfully!")
                        st.cache_data.clear()
                        st.cache_resource.clear()
                        st.rerun()
                with col2:
                    if st.button("❌ Clear Picks", use_container_width=True):
                        with st.connection("db", type="sql").session as s:
                            s.execute(text('DELETE FROM picks WHERE "user" = :user AND week = :week;'), params={"user": st.session_state.username, "week": current_week})
                            s.commit()
                        st.success("Picks cleared successfully!")
                        st.cache_data.clear()
                        st.cache_resource.clear()
                        st.rerun()
                st.divider()
                display_user_picks(st.session_state.username, current_week)

    with tab2:
        st.title("League Scoreboard")
        st.subheader("Update Weekly Scores")
        max_week = get_current_week()
        updatable_weeks = range(1, max_week + 1)
        if not updatable_weeks:
            st.info("No weeks are available to update yet.")
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
