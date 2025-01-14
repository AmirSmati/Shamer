import logging
import sqlite3
import discord
from discord.ext import commands, tasks
import pytesseract
from PIL import Image
from difflib import get_close_matches
import datetime
from dotenv import load_dotenv
import asyncio
import os
import re

# Enable logging
logging.basicConfig(level=logging.DEBUG)

# OCR function
def extract_text_from_image(image_path):
    try:
        image = Image.open(image_path)
        text = pytesseract.image_to_string(image, lang="eng+chi_sim")
        return text
    except Exception as e:
        logging.error(f"Error processing image: {e}")
        return ""

# Database setup
def init_db():
    conn = sqlite3.connect("players.db")
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS players (
                        player_name TEXT PRIMARY KEY,
                        score INTEGER DEFAULT 0,
                        timesAdded INTEGER DEFAULT 0
                    )''')
    conn.commit()
    conn.close()

def execute_query(query, params=(), fetch_one=False):
    conn = sqlite3.connect("players.db")
    cursor = conn.cursor()
    cursor.execute(query, params)
    result = cursor.fetchone() if fetch_one else cursor.fetchall()
    conn.commit()
    conn.close()
    return result

init_db()

def add_player_to_db(player_name):
    execute_query('INSERT OR IGNORE INTO players (player_name) VALUES (?)', (player_name,))

def reset_db():
    # Connect to the database
    conn = sqlite3.connect('players.db')
    cursor = conn.cursor()
    
    try:
        # Reset the `score` and `timesAdded` fields for all players
        cursor.execute('''
            UPDATE players
            SET score = 0, timesAdded = 0
        ''')
        conn.commit()  # Commit changes to the database
        print("Database reset successfully! All players' scores and timesAdded have been reset to 0.")
    except Exception as e:
        print(f"An error occurred while resetting the database: {e}")
    finally:
        conn.close()

def update_score_in_db(player_name, score):
    execute_query('''
        UPDATE players
        SET score = score + ?, timesAdded = timesAdded + 1
        WHERE player_name = ?
    ''', (score, player_name))

def find_worst_player():
    return execute_query(
        'SELECT player_name, score FROM players ORDER BY score ASC LIMIT 1', fetch_one=True
    )

def find_closest_player_name(partial_name):
    all_players = [row[0] for row in execute_query('SELECT player_name FROM players')]
    closest_matches = get_close_matches(partial_name, all_players, n=1, cutoff=0.6)
    return closest_matches[0] if closest_matches else None

def get_players_from_db():
    return execute_query('SELECT player_name, score, timesAdded FROM players')

def extract_players_and_scores(text):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    players_and_scores = []
    current_player = None
    for line in lines:
        if re.match(r"^\d+$", line):
            if current_player:
                players_and_scores.append((current_player, int(line)))
                current_player = None
        else:
            current_player = line
    return players_and_scores

# Discord bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)


@bot.command()
async def add(ctx, *, player_name: str):
    add_player_to_db(player_name)
    await ctx.send(f"Player {player_name} added!")

@bot.command()
async def reset(ctx):
    reset_db()
    await ctx.send(f"Leaderboard Reset!")

@bot.command()
async def lookFor(ctx, *, player_name: str):
    result = execute_query('SELECT score, timesAdded FROM players WHERE player_name = ?', (player_name,), fetch_one=True)
    if result:
        await ctx.send(f"{player_name} has {result[0]} points and has been added {result[1]} times.")
    else:
        await ctx.send("No such player exists in the database.")

@bot.command()
async def remove(ctx, *, player_name: str):
    execute_query('DELETE FROM players WHERE player_name = ?', (player_name,))
    await ctx.send(f"Player {player_name} removed.")

@bot.command()
async def add_score(ctx):
    print("!add_score command called")
    if len(ctx.message.attachments) == 0:
        await ctx.send("No image attached.")
        return

    attachment = ctx.message.attachments[0]
    image_path = f'./{attachment.filename}'
    await attachment.save(image_path)

    # Extract text from the image
    text = extract_text_from_image(image_path)
    print("Extracted text:", text)

    # Extract players and scores
    extracted_data = extract_players_and_scores(text)

    if not extracted_data:
        await ctx.send("No valid player names or scores found in the image.")
        return

    response_messages = []
    for player_name, score in extracted_data:
        closest_name = find_closest_player_name(player_name)
        if closest_name:
            # Update the player's score and increment timesAdded
            conn = sqlite3.connect('players.db')
            cursor = conn.cursor()
            cursor.execute('''
                UPDATE players
                SET score = score + ?, timesAdded = timesAdded + 1
                WHERE player_name = ?
            ''', (score, closest_name))
            conn.commit()
            
            # Fetch the updated score and timesAdded
            cursor.execute('SELECT score, timesAdded FROM players WHERE player_name = ?', (closest_name,))
            new_score, times_added = cursor.fetchone()
            conn.close()
            
            response_messages.append(f'{closest_name} now has {new_score} points! Times added: {times_added}')
        else:
            response_messages.append(f'Player "{player_name}" not found in the database.')

    await ctx.send("\n".join(response_messages))


@bot.command()
async def shamerOfTheWeek(ctx):
    # Connect to the database
    conn = sqlite3.connect('players.db')
    cursor = conn.cursor()
    
    # Find the player with the highest `timesAdded`
    cursor.execute("""
        SELECT player_name, score, timesAdded
        FROM players
        WHERE timesAdded > 0
        ORDER BY timesAdded DESC LIMIT 1
    """)
    result = cursor.fetchone()
    conn.close()
    
    if result:
        player_name, total_score, times_added = result
        average_score = total_score / times_added  # Calculate the average score
        await ctx.send(
            f"🏆 **Shamer of the Week** 🏆\n"
            f"Player: **{player_name}**\n"
            f"Times Added: **{times_added}**\n"
            f"Total Score: **{total_score}**\n"
            f"Average Score: **{average_score:.2f}**"
        )
    else:
        await ctx.send("No players found in the database!")


@bot.command()
async def leaderboard(ctx):
    players = get_players_from_db()
    if not players:
        await ctx.send("The leaderboard is empty. Add some players first!")
        return

    # Sort players by `timesAdded` in descending order, then by name alphabetically as a tiebreaker
    sorted_players = sorted(players, key=lambda x: (-x[2], x[0]))
    leaderboard_message = "**Leaderboard:**\n"
    leaderboard_message += "\n".join(
        f"{rank}. {name}: {score} points (Added {times_added} times)"
        for rank, (name, score, times_added) in enumerate(sorted_players, start=1)
    )
    await ctx.send(leaderboard_message)

# Load environment variables
load_dotenv()
DISCORD_KEY = os.getenv("DISCORD_API")

if DISCORD_KEY:
    bot.run(DISCORD_KEY)
else:
    print("Error: DISCORD_API key not found in environment variables.")
