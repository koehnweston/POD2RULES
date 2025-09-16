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
import matplotlib


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

def calculate_parlay_odds(picked_teams, moneyline_data):
    """Calculates the parlay odds for a list of picks, skipping teams with no odds."""
    if not picked_teams:
        return "N/A"

    total_decimal_odd = 1.0
    picks_with_odds_count = 0

    for team in picked_teams:
        american_odd = moneyline_data.get(team)
        if american_odd is None:
            continue  # Skip this team if no odds are available

        picks_with_odds_count += 1
        if american_odd > 0:
            decimal_odd = (american_odd / 100) + 1
        else:
            decimal_odd = (100 / abs(american_odd)) + 1
        total_decimal_odd *= decimal_odd

    if picks_with_odds_count == 0:
        return "N/A (No odds available)"

    if total_decimal_odd >= 2.0:
        final_american_odd = (total_decimal_odd - 1) * 100
        return f"+{final_american_odd:.0f}"
    else:
        final_american_odd = -100 / (total_decimal_odd - 1)
        return f"{final_american_odd:.0f}"

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
    """Fetches betting lines (spread and moneyline) for a given week from the API."""
    lines_data, error = fetch_api_data("lines", {'year': year, 'week': week, 'seasonType': 'regular'})
    if error or not lines_data: return {}

    betting_data = defaultdict(dict)
    for game in lines_data:
        if game.get('lines'):
            preferred_providers = ['Bovada', 'DraftKings', 'consensus']
            line_to_use = None

            for provider in preferred_providers:
                found_line = next((line for line in game['lines'] if line.get('provider') == provider), None)
                if found_line:
                    line_to_use = found_line
                    break

            if not line_to_use:
                line_to_use = game['lines'][0]

            if line_to_use.get('spread'):
                try:
                    spread = float(line_to_use['spread'])
                    betting_data[game['homeTeam']]['spread'] = spread
                    betting_data[game['awayTeam']]['spread'] = -spread
                except (ValueError, TypeError):
                    pass

            if line_to_use.get('homeMoneyline') is not None and line_to_use.get('awayMoneyline') is not None:
                betting_data[game['homeTeam']]['moneyline'] = line_to_use['homeMoneyline']
                betting_data[game['awayTeam']]['moneyline'] = line_to_use['awayMoneyline']

    return dict(betting_data)


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

        # Pivot with raw usernames first for accurate calculations
        pivot_df = df.pivot_table(index='user', columns='week', values='wins', aggfunc='sum').fillna(0)

        week_cols = sorted([col for col in pivot_df.columns if isinstance(col, (int, float))])
        pivot_df['Total Wins'] = pivot_df[week_cols].sum(axis=1)
        pivot_df.sort_values(by='Total Wins', ascending=False, inplace=True)

        st.header("🏆 League Podium")
        top_users_df = pivot_df.head(3)

        if top_users_df.empty:
            st.info("No scores yet to determine a leader.")
        else:
            cols = st.columns(len(top_users_df))
            medals = ["🥇", "🥈", "🥉"]
            for i, (user, row) in enumerate(top_users_df.iterrows()):
                with cols[i]:
                    user_status = emoji_map.get(user, '')
                    # For metrics, use a placeholder for images, otherwise show emoji
                    display_status = user_status if not user_status.startswith(':') else '⭐'
                    label_str = f"{medals[i]} {display_status} {user}".strip()
                    st.metric(
                        label=label_str,
                        value=int(row['Total Wins'])
                    )
        st.divider()
        st.subheader("Full Season Standings")

        # Reset index to convert 'user' from index to a column for easier manipulation
        pivot_df.reset_index(inplace=True)
        pivot_df.rename(columns={'user': 'User Name'}, inplace=True)

        IMAGE_MAP = {":DUMPSTER:": "DUMPSTER.png", ":CAR:": "CAR.png"}

        def get_image_path(status_val):
            path = IMAGE_MAP.get(status_val)
            return path if path and os.path.exists(path) else None

        def format_user_display(row):
            status = row['status_val']
            # If it's a standard emoji, prepend it to the name
            if status and not status.startswith(':'):
                return f"{status} {row['User Name']}".strip()
            # Otherwise (it's an image or no status), just return the name
            return row['User Name']

        # Map status values from emoji_map to each user
        pivot_df['status_val'] = pivot_df['User Name'].map(emoji_map).fillna('')
        # Create a new column with paths to images if applicable
        pivot_df['Image'] = pivot_df['status_val'].apply(get_image_path)
        # Create the final user display name
        pivot_df['User'] = pivot_df.apply(format_user_display, axis=1)

        # Prepare the final dataframe for display
        rename_dict = {col: f"Week {col}" for col in week_cols}
        pivot_df.rename(columns=rename_dict, inplace=True)

        final_week_cols = [f"Week {col}" for col in week_cols]
        # Define the final column order, with Image and User first
        display_cols = ['Image', 'User'] + final_week_cols + ['Total Wins']
        display_df = pivot_df[display_cols]
        
        # Convert numeric columns to integers for clean display
        for col in final_week_cols + ['Total Wins']:
            display_df[col] = display_df[col].astype(int)

        styled_df = display_df.style.background_gradient(
            cmap='summer_r',
            subset=['Total Wins']
        ).format(precision=0)

        st.dataframe(
            styled_df,
            use_container_width=True,
            column_config={
                "Image": st.column_config.ImageColumn("Status", width="small"),
                "User": st.column_config.TextColumn("User", width="medium"),
            },
            hide_index=True
        )

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
            betting_data = fetch_betting_lines(current_year, current_week)
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
            line = betting_data.get(team, {}).get('spread')

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
            st.data_editor(picks_df, column_config={"Select": st.column_config.CheckboxColumn("Picked", default=False)}, disabled=['Select', 'My Team', 'Location', 'Opponent', 'Line', 'Result'], hide_index=True, use_container_width=True, key=f"picks_display_{current_week}")
        else:
            st.subheader(f"Your Matchups for Week {current_week}")
            if not picks_df.empty:
                edited_df = st.data_editor(picks_df, column_config={"Select": st.column_config.CheckboxColumn("Select", default=False)}, disabled=["My Team", "Location", "Opponent", "Line", "Result"], hide_index=True, use_container_width=True, key=f"picks_editor_{current_week}")
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
        display_scoreboard()
        st.divider()

        with st.expander("🛠️ League Management Tools"):
            if st.session_state.username in ["Paul", "Weston"]:
                st.subheader("👑 Set User Status Emojis")
                
                # Map of user-friendly labels to the value that will be stored
                EMOJI_OPTIONS = {
                    "None": "None",
                    "On Fire 🔥": "🔥",
                    "Ice Cold ❄️": "❄️",
                    "Money Bags 💰": "💰",
                    "Clown 🤡": "🤡",
                    "Galaxy Brain 🧠": "🧠",
                    "Trash Can 🗑️": "🗑️",
                    "To the Moon 🚀": "🚀",
                    "Stonks Down 📉": "📉",
                    "King 👑": "👑",
                    "Subaru 🚗🔥": "🚗🔥",
                    "Dumpster Fire 🗑️🔥": "🗑️🔥",
                    "Image: Dumpster": ":DUMPSTER:", # Special identifier for image
                    "Image: Car": ":CAR:",       # Special identifier for image
                }

                with st.form("emoji_form"):
                    user_to_edit = st.selectbox("Select User", options=list(USERS.keys()))
                    # Display the labels to the user
                    selected_label = st.radio("Select Status", options=list(EMOJI_OPTIONS.keys()), horizontal=True)
                    # Get the corresponding value to store in the DB
                    emoji_to_store = EMOJI_OPTIONS[selected_label]
                    
                    if st.form_submit_button("Update Status"):
                        try:
                            with st.connection("db", type="sql").session as s:
                                s.execute(text('DELETE FROM user_status WHERE "user" = :user;'), params={"user": user_to_edit})
                                if emoji_to_store != "None":
                                    s.execute(text('INSERT INTO user_status ("user", emoji) VALUES (:user, :emoji);'), params={"user": user_to_edit, "emoji": emoji_to_store})
                                s.commit()
                            st.success(f"Status for {user_to_edit} has been updated.")
                            st.rerun()
                        except Exception as e: st.error(f"Database error: {e}")

            st.subheader("Manual Score Adjustment")
            with st.form("manual_update_form"):
                st.write("Use this form to add or update scores for weeks not covered by the API (e.g., Week 0).")
                manual_user = st.selectbox("Select User", options=list(USERS.keys()), key="manual_user_select")
                manual_week = st.number_input("Enter Week", min_value=0, step=1, value=0)
                manual_wins = st.number_input("Enter Total Wins", min_value=0, step=1)
                if st.form_submit_button("Submit Manual Score"):
                    try:
                        with st.connection("db", type="sql").session as s:
                            s.execute(text('DELETE FROM scoreboard WHERE "user" = :user AND week = :week;'), params={"user": manual_user, "week": manual_week})
                            s.execute(text('INSERT INTO scoreboard ("user", week, wins) VALUES (:user, :week, :wins);'), params={"user": manual_user, "week": manual_week, "wins": manual_wins})
                            s.commit()
                        st.success(f"Successfully updated Week {manual_week} score for {manual_user} to {manual_wins} wins.")
                        st.rerun()
                    except Exception as e: st.error(f"Failed to update database: {e}")

            st.subheader("Update Weekly Scores (Automatic)")
            max_week = get_current_week()
            updatable_weeks = range(1, max_week + 1)
            if not updatable_weeks:
                st.info("No weeks are available to update yet.")
            else:
                week_to_update = st.selectbox("Select week to update scores", options=updatable_weeks, index=len(updatable_weeks) - 1)
                if st.button(f"Calculate & Update Scores for Week {week_to_update}", type="primary"):
                    update_scoreboard(week_to_update, datetime.datetime.now().year)

        st.divider()

        st.header("🕵️‍♂️ Weekly Pick Review")
        current_year = datetime.datetime.now().year
        last_completed_week = get_current_week() - 1

        if last_completed_week < 1:
            st.info("No weeks have been completed yet for a review.")
        else:
            reviewable_weeks = range(1, last_completed_week + 1)
            review_week = st.selectbox("Select a week to review", options=reviewable_weeks, index=len(reviewable_weeks) - 1, format_func=lambda w: f"Week {w}")

            with st.spinner(f"Gathering intel and expert opinions for Week {review_week}..."):
                conn = st.connection("db", type="sql")
                all_weekly_picks_df = conn.query(f"SELECT * FROM picks WHERE week = {review_week};")
                betting_data = fetch_betting_lines(current_year, review_week)
                game_results = fetch_completed_game_scores(current_year, review_week)

            if all_weekly_picks_df.empty:
                st.warning(f"No one submitted picks for Week {review_week}, so there's nothing to review!")
            else:
                picks_by_user = all_weekly_picks_df.groupby('user')
                moneyline_odds = {team: data['moneyline'] for team, data in betting_data.items() if 'moneyline' in data}

                for user, user_picks_df in picks_by_user:
                    with st.expander(f"**{user}'s Report Card for Week {review_week}**"):
                        total_picks = len(user_picks_df)
                        correct_picks, upset_wins, favorite_losses = 0, 0, 0
                        review_data, picked_teams_list = [], []

                        if user == "Jared":
                            is_chalk_eater, is_fish_bettor = False, False
                            for _, pick_row in user_picks_df.iterrows():
                                spread = betting_data.get(pick_row['team'], {}).get('spread')
                                if spread is not None:
                                    if spread < 0: is_chalk_eater = True
                                    elif spread > 0: is_fish_bettor = True
                            if is_fish_bettor: st.warning("🐠 **Fish Bet Detected!** Jared is swimming against the current by picking an underdog.")
                            if is_chalk_eater: st.info("🍞 **Chalk Eater!** Jared is playing it safe with a favorite.")

                        for _, pick_row in user_picks_df.iterrows():
                            team = pick_row['team']
                            picked_teams_list.append(team)
                            is_correct = game_results.get(team, {}).get('win', False)
                            spread = betting_data.get(team, {}).get('spread')

                            if is_correct: correct_picks += 1
                            if is_correct and spread is not None and spread > 0: upset_wins += 1
                            if not is_correct and spread is not None and spread < 0: favorite_losses += 1

                            spread_str = f"+{spread}" if spread and spread > 0 else str(spread) if spread is not None else "N/A"
                            pick_type = "Favorite" if spread is not None and spread < 0 else "Upset Pick" if spread is not None and spread > 0 else "Even Match"
                            outcome_str = "✅ Win" if is_correct else "❌ Loss" if team in game_results else "Pending"

                            review_data.append({"Pick": team, "Spread": spread_str, "Type": pick_type, "Outcome": outcome_str})

                        parlay_str = calculate_parlay_odds(picked_teams_list, moneyline_odds)
                        st.markdown(f"##### Grade: **{correct_picks}/{total_picks}** | Hypothetical Parlay: **{parlay_str}**")

                        if correct_picks == total_picks and total_picks > 0: st.success("🔥 **Flawless Victory!** A perfect week. Are you a time traveler or just that good? Absolutely brilliant.")
                        if upset_wins > 0: st.info(f"🧠 **Galaxy Brain Alert!** You successfully called **{upset_wins} upset(s)**. You zigged when Vegas zagged. Well played.")
                        if favorite_losses > 0: st.warning(f"💥 **Bad Beat City!** You got burned by **{favorite_losses} supposed 'sure thing'(s)**. Vegas sends its 'condolences'.")
                        if correct_picks == 0 and total_picks > 0: st.error("🤡 **The Jester Award!** A bold strategy to pick all losers. It's a statement, we're just not sure what it is.")

                        st.dataframe(pd.DataFrame(review_data), hide_index=True, use_container_width=True)

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
