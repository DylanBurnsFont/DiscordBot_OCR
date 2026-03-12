# Discord Bot for Archero2 MI score reader
Reads MI scores

## TODO
- [x] Database
- [ ] Data visualization commands
- [ ] Special ChartS

## Slash command
- `/mi` now supports an optional `output_format` argument:
	- `csv` (default): returns the score CSV file
	- `chart`: returns a matplotlib chart image of scores

## Commands TODO
- Overall guild/Guild members/User damage today
- Overall guild/Guild members/User damage this week (Total and discrete)
- Overall guild/Guild members/User damage this month (Total and discrete can plot evolution of damage throughout the month)
- Overall guild/Guild members/User damage this month per boss
- Overall guild/Guild members/User damage since the start
- See who has attacked today/this week (see what days people have/haven't)
- % share of guild damage today/week for guild members
- Leaderboard to see who has used it the most, and streak to see daily streak.
- When displaying scores, add a normalization parameter to account for different power guilds