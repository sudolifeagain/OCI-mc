#!/usr/bin/env sh
set -eu

BOT_USER=ubuntu
RUNTIME_DIR=/opt/minecraft/.bot-runtime

configure_server_user() {
    server_user="$1"
    server_dir="$2"

    if ! id "$server_user" >/dev/null 2>&1; then
        useradd --system --user-group --home-dir "$server_dir" --shell /usr/sbin/nologin "$server_user"
    fi
    usermod -a -G "$server_user" "$BOT_USER"

    if [ -d "$server_dir" ]; then
        chown -R "$server_user:$server_user" "$server_dir"
        find "$server_dir" -type d -exec chmod 2770 {} +
        find "$server_dir" -type f -exec chmod 0660 {} +
        find "$server_dir" -type f -name '*.sh' -exec chmod 0770 {} +
        if [ -f "$server_dir/server.properties" ]; then
            if grep -q '^broadcast-rcon-to-ops=' "$server_dir/server.properties"; then
                sed -i 's/^broadcast-rcon-to-ops=.*/broadcast-rcon-to-ops=false/' "$server_dir/server.properties"
            else
                printf '\nbroadcast-rcon-to-ops=false\n' >> "$server_dir/server.properties"
            fi
            chmod 0640 "$server_dir/server.properties"
        fi
        rm -f "$server_dir"/.paper.pid "$server_dir"/.forge.pid "$server_dir"/.forge-alt.pid
    fi
}

install -d -m 0700 -o "$BOT_USER" -g "$BOT_USER" "$RUNTIME_DIR" "$RUNTIME_DIR/pids"
configure_server_user mc-paper /opt/minecraft/paper
configure_server_user mc-forge /opt/minecraft/forge
configure_server_user mc-forge-alt /opt/minecraft/forge-alt

if [ -f /opt/minecraft/bot/.env ]; then
    chown "$BOT_USER:$BOT_USER" /opt/minecraft/bot/.env
    chmod 0600 /opt/minecraft/bot/.env
fi
