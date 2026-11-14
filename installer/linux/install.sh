#!/bin/sh
# Per-user installer for Desktop Widgets. Never uses sudo.
# Usage: ./install.sh [--components todo,usage,desktop] [--yes] [--prefix DIR] [--autostart] [--help]
# POSIX sh (dash) on purpose: a fresh Linux machine has no Python yet.
set -eu

KEYS="todo usage desktop"
SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MANIFEST_NAME=install-manifest.txt

die() { echo "install.sh: $*" >&2; exit 1; }

# ---- our own home folder ---------------------------------------------------
# Everything below starts in $HOME, so a missing or odd HOME must stop us now,
# with a message that says what to do (findings B-07 and B-11).
[ -n "${HOME:-}" ] || die "HOME is not set. Set HOME to your home folder first, then run this again."
case $HOME in
    /*) ;;
    *) die "HOME is not an absolute path. Set HOME to your home folder first, then run this again.";;
esac
case $HOME in
    *[\'\"\\\$\`%]*) die "your home folder may not contain quotes, backslash, dollar, backtick or percent signs";;
esac

# ---- where things go -------------------------------------------------------
# The XDG rules say a relative folder is ignored, and the apps ignore it too,
# so the installer must look in the same place as the apps (finding B-03).
case ${XDG_DATA_HOME:-} in
    /*) DATA_HOME=$XDG_DATA_HOME;;
    *) DATA_HOME=$HOME/.local/share;;
esac
case ${XDG_CONFIG_HOME:-} in
    /*) CONFIG_HOME=$XDG_CONFIG_HOME;;
    *) CONFIG_HOME=$HOME/.config;;
esac
BIN_DIR=$HOME/.local/bin
PREFIX=$DATA_HOME/DesktopWidgets

# Names are fixed: key -> binary / title.
binary_of() { case $1 in todo) echo TodoWidget;; usage) echo UsageMonitor;; desktop) echo DesktopMonitor;; esac; }
title_of() { case $1 in todo) echo "To-Do Widget";; usage) echo "Usage Monitor";; desktop) echo "Desktop Monitor";; esac; }

usage() {
    cat <<USAGE
Install Desktop Widgets for the current user (no sudo).

  --components LIST  comma list of: todo, usage, desktop (default: all three)
  --yes              no questions; use the defaults and any other flags
  --prefix DIR       where to install (default: $DATA_HOME/DesktopWidgets)
  --autostart        also start the chosen widgets when you sign in
  --help             show this text
USAGE
}

COMPONENTS=""
COMPONENTS_GIVEN=0
YES=0
AUTOSTART=0
while [ $# -gt 0 ]; do
    case $1 in
        --components)
            [ $# -ge 2 ] || die "--components needs a value"
            COMPONENTS=$2
            COMPONENTS_GIVEN=1      # remember it even when the value is empty
            shift 2;;
        --yes|-y) YES=1; shift;;
        --prefix) [ $# -ge 2 ] || die "--prefix needs a folder"; PREFIX=$2; shift 2;;
        --autostart) AUTOSTART=1; shift;;
        --help|-h) usage; exit 0;;
        *) usage >&2; die "unknown option: $1";;
    esac
done

[ "$(id -u)" -ne 0 ] || die "do not run this as root; it installs for your own user."
[ -d "$SELF_DIR/DesktopWidgets" ] || die "run this from the extracted folder (DesktopWidgets/ not found next to it)."

# Paths end up inside launcher scripts and .desktop files, so keep them plain.
case $PREFIX in
    /*) ;;
    *) die "--prefix must be an absolute path";;
esac
case $PREFIX in
    *[\'\"\\\$\`%]*) die "the install folder may not contain quotes, backslash, dollar, backtick or percent signs";;
esac
case $PREFIX in ""|/|"$HOME"|"$HOME/") die "refusing to install into '$PREFIX'";; esac

# Existing folder: only upgrade one that we made (it has our manifest).
if [ -e "$PREFIX" ] && [ -n "$(ls -A "$PREFIX" 2>/dev/null)" ] && [ ! -f "$PREFIX/$MANIFEST_NAME" ]; then
    die "$PREFIX exists and is not a Desktop Widgets install; choose another --prefix."
fi

# ---- choose widgets -------------------------------------------------------
# Only ask when nobody gave a list. "--components ''" means "install nothing",
# not "install everything" (finding B-08).
if [ "$COMPONENTS_GIVEN" -eq 0 ]; then
    COMPONENTS="todo,usage,desktop"
    if [ "$YES" -eq 0 ] && [ -t 0 ]; then
        picked=""
        if command -v zenity >/dev/null 2>&1 && { [ -n "${WAYLAND_DISPLAY:-}" ] || [ -n "${DISPLAY:-}" ]; }; then
            picked=$(zenity --list --checklist --title="Desktop Widgets" \
                --text="Which widgets should be installed?" \
                --column=Install --column=Key --column=Widget --hide-column=2 --print-column=2 \
                --separator=, TRUE todo "To-Do Widget" TRUE usage "Usage Monitor" \
                TRUE desktop "Desktop Monitor" 2>/dev/null) || picked=""
        fi
        if [ -z "$picked" ]; then
            for key in $KEYS; do
                printf 'Install %s? [Y/n] ' "$(title_of "$key")"
                read -r answer || answer=""
                case $answer in [nN]*) ;; *) picked="${picked:+$picked,}$key";; esac
            done
        fi
        COMPONENTS=$picked
    fi
fi

CHOSEN=""
for key in $(echo "$COMPONENTS" | tr ',' ' '); do
    case $key in
        todo|usage|desktop) case " $CHOSEN " in *" $key "*) ;; *) CHOSEN="$CHOSEN $key";; esac;;
        *) die "unknown component '$key' (use todo, usage, desktop)";;
    esac
done
CHOSEN=${CHOSEN# }
[ -n "$CHOSEN" ] || die "nothing chosen, nothing installed."
echo "Installing: $CHOSEN"
echo "Folder:     $PREFIX"

# ---- does a file belong to this install? ----------------------------------
# A launcher or an autostart file names our folder directly. A menu entry names
# our launcher, so that launcher must point at our folder. This keeps our
# scripts away from another install's files (finding B-04).
entry_is_ours() { # $1 = file
    [ -f "$1" ] || return 1
    # The trailing slash keeps "DesktopWidgets" from matching "DesktopWidgetsTwo".
    grep -qF "$PREFIX/" "$1" 2>/dev/null && return 0
    exec_value=$(sed -n 's/^Exec=//p' "$1" 2>/dev/null | head -n 1)
    case $exec_value in
        "\"$BIN_DIR"/desktopwidgets-*) ;;
        *) return 1;;
    esac
    launcher=$(printf '%s\n' "$exec_value" | sed 's/^"//; s/"$//')
    [ -f "$launcher" ] && grep -qF "$PREFIX/" "$launcher" 2>/dev/null
}

remove_ours() { # $1 = file; leave another install's file alone
    if entry_is_ours "$1"; then rm -f "$1"; fi
}

# ---- copy the bundle (re-running upgrades in place) -----------------------
# The manifest is written before anything is copied, so a copy that fails still
# leaves a folder the uninstaller recognises and can clean up (finding B-06).
# It also names the install folder itself (finding B-10).
UPGRADE=0
if [ -f "$PREFIX/$MANIFEST_NAME" ]; then UPGRADE=1; fi
mkdir -p "$PREFIX"
MANIFEST=$PREFIX/$MANIFEST_NAME
{
    echo "version=2"
    echo "prefix=$PREFIX"
    echo "folder=$PREFIX"
    echo "components=$CHOSEN"
} > "$MANIFEST"

if [ "$UPGRADE" -eq 1 ]; then
    echo "Upgrading the existing install."
    rm -rf "${PREFIX:?}/_internal"
    for key in $KEYS; do rm -f "${PREFIX:?}/$(binary_of "$key")"; done
fi
mkdir -p "$PREFIX/icons" "$BIN_DIR" "$DATA_HOME/applications"
cp -R "$SELF_DIR/DesktopWidgets/." "$PREFIX/"
cp "$SELF_DIR/icons/"*.png "$PREFIX/icons/"
cp "$SELF_DIR/uninstall.sh" "$PREFIX/uninstall.sh"
chmod 755 "$PREFIX/uninstall.sh"

# ---- launchers, menu entries, autostart -----------------------------------
# Quote one path for an Exec= line the way the Desktop Entry rules ask (and the
# way the apps do): double the percent sign and escape \ " ` and $ (B-07).
exec_quote() {
    escaped=$(printf '%s\n' "$1" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/`/\\`/g' -e 's/\$/\\$/g' -e 's/%/%%/g')
    printf '"%s"\n' "$escaped"
}

# The menu entry is a richer file (icon, category) that the apps never touch.
write_entry() { # $1 = file, $2 = key, $3 = exec path
    {
        echo "[Desktop Entry]"
        echo "Type=Application"
        echo "Name=Desktop Widgets: $(title_of "$2")"
        echo "Comment=$(title_of "$2") desktop widget"
        echo "Exec=$(exec_quote "$3")"
        echo "Icon=$PREFIX/icons/desktopwidgets-$2.png"
        echo "Terminal=false"
        echo "Categories=Utility;"
    } > "$1"
}

# The apps write the same autostart file from their "Start when I sign in"
# toggle, so our text must match theirs or the two overwrite each other (B-09).
# These strings are copied from the three apps; do not edit the apps here.
autostart_name_of() { case $1 in todo) echo "To-Do Widget";; usage) echo "Usage Monitor";; desktop) echo "Desktop Monitor";; esac; }
autostart_comment_of() {
    case $1 in
        todo) echo "Desktop to-do list and goals widget";;
        usage) echo "AI usage and credits desktop widget";;
        desktop) echo "Desktop Monitor card (CPU, GPU, RAM, disks)";;
    esac
}
# The Usage Monitor leaves a plain path unquoted; the other two always quote it.
autostart_exec() { # $1 = key, $2 = binary path
    case $1 in
        usage) case $2 in *[!A-Za-z0-9_/.+-]*) exec_quote "$2";; *) printf '%s\n' "$2";; esac;;
        *) exec_quote "$2";;
    esac
}
write_autostart() { # $1 = file, $2 = key, $3 = binary path
    {
        echo "[Desktop Entry]"
        echo "Type=Application"
        echo "Name=$(autostart_name_of "$2")"
        echo "Comment=$(autostart_comment_of "$2")"
        echo "Exec=$(autostart_exec "$2" "$3")"
        echo "Terminal=false"
        echo "X-GNOME-Autostart-enabled=true"
    } > "$1"
}

# A component that is not chosen loses its binary, and also the launcher, menu
# entry and autostart entry an earlier install left behind (finding B-02).
# The menu entry is checked first: it is only ours while its launcher still
# points at this folder, so the launcher is removed last.
for key in $KEYS; do
    case " $CHOSEN " in
        *" $key "*) ;;
        *)
            remove_ours "$DATA_HOME/applications/desktopwidgets-$key.desktop"
            remove_ours "$CONFIG_HOME/autostart/desktopwidgets-$key.desktop"
            remove_ours "$BIN_DIR/desktopwidgets-$key"
            rm -f "${PREFIX:?}/$(binary_of "$key")"
            ;;
    esac
done

for key in $CHOSEN; do
    launcher=$BIN_DIR/desktopwidgets-$key
    printf '#!/bin/sh\nexec "%s/%s" "$@"\n' "$PREFIX" "$(binary_of "$key")" > "$launcher"
    chmod 755 "$launcher"
    entry=$DATA_HOME/applications/desktopwidgets-$key.desktop
    write_entry "$entry" "$key" "$launcher"
    echo "file=$launcher" >> "$MANIFEST"
    echo "file=$entry" >> "$MANIFEST"
    if [ "$AUTOSTART" -eq 1 ]; then
        mkdir -p "$CONFIG_HOME/autostart"
        auto=$CONFIG_HOME/autostart/desktopwidgets-$key.desktop
        write_autostart "$auto" "$key" "$PREFIX/$(binary_of "$key")"
        echo "file=$auto" >> "$MANIFEST"
    fi
done
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA_HOME/applications" 2>/dev/null || true

# ---- plain-words warnings --------------------------------------------------
if ! command -v gnome-keyring-daemon >/dev/null 2>&1 && ! command -v keepassxc >/dev/null 2>&1 \
   && ! command -v kwalletd5 >/dev/null 2>&1 && ! command -v kwalletd6 >/dev/null 2>&1 \
   && [ ! -f /usr/share/dbus-1/services/org.freedesktop.secrets.service ]; then
    echo "WARNING: no Secret Service (key store) found. Provider keys cannot be saved until you install one,"
    echo "         for example: sudo apt install gnome-keyring libsecret-1-0"
fi
case ${XDG_CURRENT_DESKTOP:-} in
    *GNOME*)
        if command -v gnome-extensions >/dev/null 2>&1 \
           && ! gnome-extensions list --enabled 2>/dev/null | grep -qi appindicator; then
            echo "NOTE: the GNOME AppIndicator extension is not enabled, so there is no tray icon."
            echo "      The widgets still work: right-click the card header, or start with --settings."
        fi;;
esac
case ":${PATH}:" in *":$BIN_DIR:"*) ;; *) echo "NOTE: $BIN_DIR is not on your PATH; use the menu entries or add it.";; esac

echo "Done. Installed: $CHOSEN. Start them from the application menu or run desktopwidgets-<todo|usage|desktop>."
