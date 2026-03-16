# Discord Bot for Archero2 MI score reader
Reads MI scores

## TODO
- [x] Database
- [x] Data visualization commands
- [x] Special ChartS
- [x] Make the timed messages explicit to a certain person and to their assigned guild
- [x] Multi-guild support with role-based member detection
- [ ] % share of guild damage today/week for guild members
- [ ] When displaying scores, add a normalization parameter to account for different power guilds
- [ ] Damage evolution over the weeks to track progress

## Slash command
- `/mi` now supports an optional `output_format` argument:
	- `csv` (default): returns the score CSV file
	- `chart`: returns a matplotlib chart image of scores

## Commands TODO
- [x] Overall guild/Guild members/User damage today
- [x] Overall guild/Guild members/User damage this week (Total and discrete)
- [x] Overall guild/Guild members/User damage this month (Total and discrete can plot evolution of damage throughout the month)
- [X] Overall guild/Guild members/User damage this month per boss
- [X] Overall guild/Guild members/User damage since the start
- [x] See who hasn't attacked today/this week (see what days people have/haven't)
- [x] Leaderboard to see who has used it the most, and streak to see daily streak.
- [x] Add timed message after reset with the previous day's scores and @ someone
- [x] Make the timed messages explicit to a certain person and to their assigned guild
- [ ] % share of guild damage today/week for guild members
- [ ] Damage evolution over the weeks to track progress
- [ ] Prayer leaderboard

## Multi-Guild Configuration

The bot now supports multiple guilds with automatic daily damage reports. Each guild can have its own channel and role-based member detection.

### Current Configuration
- **AboveAll**: Default guild configuration using existing environment variables

### Adding New Guilds

**Step 1: Add Environment Variables to `.env` file:**
```env
# Your existing variables
MI_REPORT_CHANNEL_ID=123456789012345678
DISCORD_OWNER_ID=987654321098765432

# Add these for each new guild:
SECOND_GUILD_CHANNEL_ID=111111111111111111  # Discord channel ID for reports
SECOND_GUILD_OWNER_ID=222222222222222222    # Discord user ID for mentions
```

**Step 2: Update Guild Configuration** in `src/cogs/report.py`:
```python
GUILD_CONFIGS = {
    "AboveAll": {
        "channel_id_env": "MI_REPORT_CHANNEL_ID",
        "role_name": "AboveAll",
        "owner_id_env": "DISCORD_OWNER_ID"
    },
    # Add your new guild here:
    "YourGuildName": {
        "channel_id_env": "SECOND_GUILD_CHANNEL_ID", 
        "role_name": "YourDiscordRoleName",
        "owner_id_env": "SECOND_GUILD_OWNER_ID"
    }
}
```

**Step 3: Create Discord Role:**
- Create a Discord role with the exact name specified in `role_name`
- Assign this role to all guild members

### Commands

**For Users:**
- `/manual-report [guild_name]` - Manually trigger damage heatmap report
- `/manual-report` - Auto-detect guild from your roles and generate report

**For Admins:**
- `/guild-status` - View configuration status for all guilds (admin only)

### Features

- **Automatic Daily Reports**: Sent at 11:00 AM Europe/Madrid timezone
- **Role-Based Detection**: Users are automatically assigned to guilds based on Discord roles
- **Heatmap Visualization**: Color-coded damage tables (red=low, green=high)
- **Per-Day Scaling**: Each day's colors are independently scaled for better visualization
- **Special User Handling**: KNUBE (ID: 1391487700242141347) gets bonus damage from top performers

### How It Works

1. **Daily Task**: Runs every day at configured time
2. **Guild Detection**: Users are mapped to game guilds via Discord roles
3. **Report Generation**: Creates damage heatmaps using matplotlib
4. **Channel Delivery**: Sends reports to each guild's designated channel
5. **Owner Mentions**: Tags the guild owner in each report