# 🧊 IceBeat

A music player Discord bot powered by [Lavalink](https://lavalink.dev/).

1. [Commands](#commands)<br>
1.1. [Bot Owner](#bot-owner)<br>
1.2. [Server Related](#bot-owner)<br>
2. [Configuration Description & Example](#configuration-description-%26-example)<br>
3. [Installation](#installation)<br>
4. [Command Line Arguments](#command-line-arguments)<br>
5. [Manual Database Setup](#database-setup)<br>

## Commands

There're two command groups. Owner commands are restricted to the bot operator, while the remaining ones are installed on whitelisted servers. In short, the sole purpose of whitelisting a server is to restrict the computing resources demanded by the bot, as it targets environments where hardware is a limiting factor (e.g., Raspberry PI).

### Bot Owner

Bot Owner commands are used to manage the whitelist and can only be executed via direct messages.

Subcommands | Description
-|-
`/whitelist show` | Displays whitelisted servers
`/whitelist add <server>` | Whitelists a server
`/whitelist remove <server>` | Removes a server from the whitelist
`/whitelist sync <server (optional)>` | Updates slash commands for a whitelisted server or synchronizes them globally

Issuing `/whitelist` without additional parameters lists the  subcommands described above. _\<server\>_ parameter can be either the server name or ID (the latter is preferred).

### Server Related

There's a subset of commands that only the server owner and members of an assigned role are allowed to execute: `/shuffle`, `/loop`, `/volume`, `/filter`, `/presence stay` and `/presence leave`. Apart from those, `/staff set` and `/staff unset` let the server owner to register or remove such role. If a role isn't defined, only the server owner can execute the restricted commands.

- `/play` Requests something to play
  - Arguments
    - `query` Youtube/Spotify link or normal search as if you were on YouTube; it supports autocompletion when searching (links are ignored by this little feature)
- `/stop` Stops the player
- `/resume` Resumes the player
- `/skip` Skips the current track
- `/jump` Skips to a given queued track
  - Arguments
    - `position` track position in queue; autocompletion will display the track name after entering the position
- `/pop` Removes a track from queue given its position
  - Arguments
    - `position` track position in queue; autocompletion will display the track name after entering the position
- `/seek` Seeks to a given position in the track
  - Arguments
    - `position` track position like in the YouTube video player, for example _5:38_
- `/current` Displays current track
- `/queue` Lists queued tracks
- `/clear` Removes all queued tracks
- `/leave` Disconnects the bot from the voice channel
- `/shuffle` Toggles queue's shuffle mode
- `/loop` Toggles queue's loop mode
- `/volume` Changes player volume
  - Arguments
    - `level` volume level (the higher, the worst); only allows values between _0_ and _100_, included
- `/filter` Sets player filter
  - Arguments
    - `name` filter name; allowed values: _normal_, _bassboost_, _pop_, _soft_, _treblebass_, _eightd_, _karaoke_, _vaporwave_ and _nightcore_
- `/presence` Changes player volume
  - Subcommands
    - `stay` Bot won’t leave the voice channel when the queue's empty
    - `leave` Bot will leave the voice channel when the queue's empty
- `/staff` Changes player volume
  - Subcommands
    - `set` Sets staff role
      - Arguments
        - `role` staff role
    - `unset` Removes staff role (only the server owner will be allowed to configure the player)
    - `commands` Lists staff commands
- `/player` Displays player info

## Configuration Description & Example

Configuration is written using the [INI file](https://en.wikipedia.org/wiki/INI_file) format.

```ini
[bot]
# Discord token
token = token code
# Bot description displyed in its profile
description = anything you want
# Bot activity displayed in its profile
activity = anything you want

[player]
# Max number of enqueued tracks
queue_size = 100

[lavalink]
# Node name
name = anything you want
# Node host
host = localhost
# Node port
port = 2333
# Node password
password = str
# Node region code to assign
region = str

[database]
# SQLite file
uri = icebeat.db

[cache]
# Max number of cached entries
entries = 100
# Time to live in seconds per cache entry
ttl = 3600

[commands]
# Number of command calls per second before triggering cooldown
coolwdown_rate = 10
# Commands cooldown in secconds
cooldown_time = 10
````

Command cooldown is applied per server. Therefore, calling different commands by multiple users in a short period of time may trigger cooldown.

## Installation

IceBeat requires [Python >= 3.10](https://www.python.org/downloads/release/python-3100/) and [Lavalink](https://lavalink.dev/). After Python have been installed, you can install the bot by executing the following from the project root directory:

```sh
pip install -e .
```

`db/icebeat.db` includes a preconfigured SQLite database with all required tables already set up.

## Command Line Arguments

```text
-h, --help           show this help message and exit
-c, --config CONFIG  config file path (default: config.ini)
-v, --verbose        output logs of internal components
-d, --debug          output debugging logs
```


## Manual Database Setup

Database migrations located in `db/migrations` are required to create the tables. The setup below uses [migrate](https://github.com/golang-migrate/migrate) to write tables to an empty file. In order to use migrate with the SQLite driver, you need to have a [Go](https://go.dev/) compiler installed.

```sh
# migrate installation
go install -tags 'sqlite' github.com/golang-migrate/migrate/v4/cmd/migrate@v4.19.1

# Create database file
touch icebeat.db

# Create tables
migrate -source file://db/migrations -database sqlite://icebeat.db up

# Remove tables
migrate -source file://db/migrations -database sqlite://icebeat.db down
```
