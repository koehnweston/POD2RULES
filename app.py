import streamlit as st
import pandas as pd
import requests
import datetime
import re
import os
import pytz
import time
from collections import defaultdict
from sqlalchemy import text
import pprint


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


def are_picks_locked(week, year):
    """Checks if the current time is past the 10:59 AM pick deadline."""
    try:
        central_tz = pytz.timezone("America/Chicago")
        season_start_date = datetime.date(year, 8, 27)
        days_until_saturday = (5 - season_start_date.weekday() + 7) % 7
        first_saturday = season_start_date + datetime.timedelta(days=days_until_saturday)
        target_saturday = first_saturday + datetime.timedelta(weeks=week - 1)
        lock_time = datetime.time(10, 59)
        lock_datetime_naive = datetime.datetime.combine(target_saturday, lock_time)
        lock_datetime_aware = central_tz.localize(lock_datetime_naive)
        now_aware = datetime.datetime.now(central_tz)
        return now_aware >= lock_datetime_aware
    except Exception as e:
        st.error(f"Error checking lock time: {e}")
        return False

# --- API & Data Fetching Functions ---

def fetch_api_data(endpoint, params):
    """Generic function to fetch data from the collegefootballdata API."""
    try:
        api_key = st.secrets.api_key
        if not api_key:
            st.error("API key is present but has no value.")
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

@st.cache_data(ttl=300)
def fetch_game_results(year, week):
    """Fetches game results for a given week and returns a set of winning teams."""
    games_data, error = fetch_api_data("games", {'year': year, 'week': week, 'seasonType': 'regular'})
    if error:
        print(f"Failed to fetch game results: {error}")
        return set()
    if not games_data:
        return set()
    winning_teams = set()
    for game in games_data:
        if game.get('completed') and game.get('homePoints') is not None and game.get('awayPoints') is not None:
            if game['homePoints'] > game['awayPoints']:
                winning_teams.add(game['homeTeam'])
            elif game['awayPoints'] > game['homePoints']:
                winning_teams.add(game['awayTeam'])
    return winning_teams

@st.cache_data(ttl=300)
def fetch_completed_game_scores(year, week):
    """Fetches completed games and returns a dictionary with detailed scores."""
    games_data, error = fetch_api_data("games", {'year': year, 'week': week, 'seasonType': 'regular'})
    if error or not games_data:
        return {}
    
    scores = {}
    for game in games_data:
        if game.get('completed') and game.get('homePoints') is not None and game.get('awayPoints') is not None:
            home_team, away_team = game['homeTeam'], game['awayTeam']
            home_pts, away_pts = game['homePoints'], game['awayPoints']
            
            scores[home_team] = {'score': home_pts, 'opponent_score': away_pts, 'win': home_pts > away_pts}
            scores[away_team] = {'score': away_pts, 'opponent_score': home_pts, 'win': away_pts > home_pts}
    return scores

@st.cache_data(ttl=3600)
def fetch_betting_lines(year, week):
    """Fetches betting lines for a given week from the API."""
    lines_data, error = fetch_api_data("lines", {'year': year, 'week': week, 'seasonType': 'regular'})
    if error or not lines_data: return {}
    betting_lines = {}
    for game in lines_data:
        if game.get('lines'):
            consensus = next((line for line in game['lines'] if line.get('provider') == 'consensus'), game['lines'][0])
            if consensus and consensus.get('spread'):
                spread = float(consensus['spread'])
                betting_lines[game['homeTeam']] = spread
                betting_lines[game['awayTeam']] = -spread
    return betting_lines

# --- Scoreboard Logic (with SQL Database) ---

def update_scoreboard(week, year):
    """Calculates scores for a week and updates the database."""
    conn = st.connection("db", type="sql")
    with st.spinner(f"Fetching winners and calculating scores for Week {week}..."):
        winning_teams = fetch_game_results(year, week)
        if not winning_teams:
            st.warning(f"No completed game results found for Week {week} to update scoreboard.")
            return

        all_picks_df = conn.query(f"SELECT * FROM picks WHERE week = {week};")
        if all_picks_df.empty:
            st.warning(f"No user picks found for Week {week}.")
            return

        scores = {user: sum(1 for team in all_picks_df[all_picks_df["user"] == user]["team"] if team in winning_teams) for user in all_picks_df["user"].unique()}

        with conn.session as s:
            s.execute(text(f"DELETE FROM scoreboard WHERE week = {week};"))
            for user, wins in scores.items():
                s.execute(text('INSERT INTO scoreboard ("user", week, wins) VALUES (:user, :week, :wins);'), params=dict(user=user, week=week, wins=wins))
            s.commit()
        st.success(f"Scoreboard successfully updated for Week {week}!")
        st.cache_data.clear(); st.cache_resource.clear()

def display_scoreboard():
    """Loads scoreboard data and displays a leaderboard and a styled table."""
    try:
        conn = st.connection("db", type="sql")
        
        with conn.session as s:
            s.execute(text('CREATE TABLE IF NOT EXISTS user_status ("user" TEXT PRIMARY KEY, emoji TEXT);'))
            s.commit()

        status_df = conn.query("SELECT * FROM user_status;")
        emoji_map = {row['user']: row['emoji'] for _, row in status_df.iterrows()} if not status_df.empty else {}

        df = conn.query("SELECT * FROM scoreboard;")
        if df.empty:
            st.info("Scoreboard is empty. Submit picks or add a manual score to begin.")
            return

        df['user'] = df['user'].apply(lambda user: f"{emoji_map.get(user, '')} {user}".strip())

        df.rename(columns={'user': 'User', 'week': 'Week', 'wins': 'Wins'}, inplace=True)
        pivot_df = df.pivot_table(index='User', columns='Week', values='Wins', aggfunc='sum').fillna(0)
        
        week_cols = sorted([col for col in pivot_df.columns if isinstance(col, (int, float))])
        
        pivot_df['Total Wins'] = pivot_df[week_cols].sum(axis=1)
        pivot_df.sort_values(by='Total Wins', ascending=False, inplace=True)
        
        # --- NEW: Leaderboard/Podium Section ---
        st.header("🏆 League Podium")
        top_users = pivot_df.head(3)
        
        if top_users.empty:
            st.info("No scores yet to determine a leader.")
        else:
            cols = st.columns(len(top_users))
            medals = ["🥇", "🥈", "🥉"]
            for i, (index, row) in enumerate(top_users.iterrows()):
                with cols[i]:
                    st.metric(
                        label=f"{medals[i]} {index}",
                        value=int(row['Total Wins'])
                    )

        st.divider()

        # --- Main Table with Styling ---
        st.subheader("Full Season Standings")
        rename_dict = {col: f"Week {col}" for col in week_cols}
        pivot_df.rename(columns=rename_dict, inplace=True)
        
        final_week_cols = [f"Week {col}" for col in week_cols]
        final_cols = final_week_cols + ['Total Wins']
        display_df = pivot_df[final_cols].astype(int)
        
        # Apply a background gradient to the 'Total Wins' column
        styled_df = display_df.style.background_gradient(
            cmap='summer_r',
            subset=['Total Wins']
        ).format(precision=0)
            
        st.dataframe(styled_df, use_container_width=True)

    except Exception as e:
        st.error(f"Could not connect to or read from the database: {e}")


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
        my_teams_df = pd.DataFrame(st.session_state.my_teams, columns=["Team"])
        st.dataframe(my_teams_df, hide_index=True, use_container_width=True)
        st.divider()
        if st.button("Logout", use_container_width=True):
            st.session_state.clear()
            st.rerun()

    tab1, tab2 = st.tabs(["✍️ Weekly Picks", "🏆 Scoreboard"])

    with tab1:
        st.title("Weekly Picks Selection")
        current_year = datetime.datetime.now().year
        current_week = int(st.selectbox(
            "Select Week",
            options=[f"Week {i}" for i in range(1, 16)],
            index=get_current_week() - 1,
            key="week_selector_tab1"
        ).split(" ")[1])

        with st.spinner(f"Fetching data for Week {current_week}..."):
            betting_lines = fetch_betting_lines(current_year, current_week)
            completed_scores = fetch_completed_game_scores(current_year, current_week)
            conn = st.connection("db", type="sql")
            existing_picks_df = conn.query('SELECT team FROM picks WHERE "user" = :user AND week = :week;', params={"user": st.session_state.username, "week": current_week})
            existing_picks = set(existing_picks_df['team'])
            
            game_info = {}
            try:
                schedule_df = pd.read_csv(f"{current_year}_week_{current_week}.csv")
                for _, row in schedule_df.iterrows():
                    game_info[row['homeTeam']] = {'opponent': row['awayTeam'], 'location': 'Home'}
                    game_info[row['awayTeam']] = {'opponent': row['homeTeam'], 'location': 'Away'}
            except FileNotFoundError:
                st.warning(f"Schedule file '{current_year}_week_{current_week}.csv' not found.")

        picks_data = []
        for team in st.session_state.my_teams:
            match_details = game_info.get(team, {})
            line = betting_lines.get(team)
            
            result_str = "Pending"
            if team in completed_scores:
                game_result = completed_scores[team]
                result_char = "W" if game_result['win'] else "L"
                result_str = f"{result_char} ({game_result['score']}-{game_result['opponent_score']})"

            picks_data.append({
                "Select": team in existing_picks,
                "My Team": team,
                "Location": match_details.get('location', 'N/A'),
                "Opponent": match_details.get('opponent', 'BYE WEEK'),
                "Line": f"+{line}" if line and line > 0 else str(line) if line is not None else "N/A",
                "Result": result_str
            })
        
        cols_order = ['Select', 'My Team', 'Location', 'Opponent', 'Line', 'Result']
        picks_df = pd.DataFrame(picks_data)[cols_order] if picks_data else pd.DataFrame(columns=cols_order)
        
        picks_are_locked = are_picks_locked(current_week, current_year)
        
        if picks_are_locked:
            st.warning(f"🔒 Picks for Week {current_week} are locked.")
            st.subheader(f"Your Matchups & Results for Week {current_week}")
            st.data_editor(
                picks_df,
                column_config={"Select": st.column_config.CheckboxColumn("Picked", default=False)},
                disabled=['Select', 'My Team', 'Location', 'Opponent', 'Line', 'Result'],
                hide_index=True, use_container_width=True, key=f"picks_display_{current_week}"
            )
        else:
            st.subheader(f"Your Matchups for Week {current_week}")
            if not picks_df.empty:
                edited_df = st.data_editor(
                    picks_df,
                    column_config={"Select": st.column_config.CheckboxColumn("Select", default=False)},
                    disabled=["My Team", "Location", "Opponent", "Line", "Result"],
                    hide_index=True, use_container_width=True, key=f"picks_editor_{current_week}"
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
                        st.cache_data.clear(); st.cache_resource.clear(); st.rerun()
                with col2:
                    if st.button("❌ Clear Picks", use_container_width=True):
                        with st.connection("db", type="sql").session as s:
                            s.execute(text('DELETE FROM picks WHERE "user" = :user AND week = :week;'), params={"user": st.session_state.username, "week": current_week})
                            s.commit()
                        st.success("Picks cleared successfully!")
                        st.cache_data.clear(); st.cache_resource.clear(); st.rerun()

    with tab2:
        st.title("🏈 League Scoreboard")
        
        # Display the main scoreboard content first
        display_scoreboard()
        
        st.divider()

        # Group all management tools into a single expander
        with st.expander("🛠️ League Management Tools"):
            
            # Admin Panel for emojis
            if st.session_state.username in ["Paul", "Weston"]:
                st.subheader("👑 Set User Status Emojis")
                with st.form("emoji_form"):
                    user_to_edit = st.selectbox("Select User", options=list(USERS.keys()))
                    emoji = st.radio(
                        "Select Status", 
                        options=["None", "🔥", "❄️", "💰", "🤡"], 
                        horizontal=True
                    )
                    submitted = st.form_submit_button("Update Status")
                    if submitted:
                        try:
                            with st.connection("db", type="sql").session as s:
                                s.execute(text('DELETE FROM user_status WHERE "user" = :user;'), params={"user": user_to_edit})
                                if emoji != "None":
                                    s.execute(text('INSERT INTO user_status ("user", emoji) VALUES (:user, :emoji);'), params={"user": user_to_edit, "emoji": emoji})
                                s.commit()
                            st.success(f"Status for {user_to_edit} has been updated.")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Database error: {e}")

            # Manual Score Adjustment
            st.subheader("Manual Score Adjustment")
            with st.form("manual_update_form"):
                st.write("Use this form to add or update scores for weeks not covered by the API (e.g., Week 0).")
                manual_user = st.selectbox("Select User", options=list(USERS.keys()), key="manual_user_select")
                manual_week = st.number_input("Enter Week", min_value=0, step=1, value=0)
                manual_wins = st.number_input("Enter Total Wins", min_value=0, step=1)
                submitted = st.form_submit_button("Submit Manual Score")
                if submitted:
                    try:
                        with st.connection("db", type="sql").session as s:
                            s.execute(text('DELETE FROM scoreboard WHERE "user" = :user AND week = :week;'), params={"user": manual_user, "week": manual_week})
                            s.execute(text('INSERT INTO scoreboard ("user", week, wins) VALUES (:user, :week, :wins);'), params={"user": manual_user, "week": manual_week, "wins": manual_wins})
                            s.commit()
                        st.success(f"Successfully updated Week {manual_week} score for {manual_user} to {manual_wins} wins.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed to update database: {e}")
            
            # Update Weekly Scores
            st.subheader("Update Weekly Scores (Automatic)")
            max_week = get_current_week()
            updatable_weeks = range(1, max_week + 1)
            if not updatable_weeks:
                st.info("No weeks are available to update yet.")
            else:
                week_to_update = st.selectbox("Select week to update scores", options=updatable_weeks, index=len(updatable_weeks) - 1)
                if st.button(f"Calculate & Update Scores for Week {week_to_update}", type="primary"):
                    update_scoreboard(week_to_update, datetime.datetime.now().year)

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
