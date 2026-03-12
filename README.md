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
- [x] Overall guild/Guild members/User damage today
- [x] Overall guild/Guild members/User damage this week (Total and discrete)
- [x] Overall guild/Guild members/User damage this month (Total and discrete can plot evolution of damage throughout the month)
- [X] Overall guild/Guild members/User damage this month per boss
- [X] Overall guild/Guild members/User damage since the start
- [ ] See who has attacked today/this week (see what days people have/haven't)
- [ ] % share of guild damage today/week for guild members
- [X] Leaderboard to see who has used it the most, and streak to see daily streak.
- [ ] When displaying scores, add a normalization parameter to account for different power guilds