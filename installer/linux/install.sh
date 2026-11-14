#!/bin/sh
# Per-user installer for Desktop Widgets. Never uses sudo.
# Usage: ./install.sh [--components todo,usage,desktop] [--yes] [--prefix DIR] [--autostart] [--help]
set -eu

KEYS="todo usage desktop"
SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
BIN_DIR=$HOME/.local/bin
PREFIX=$DATA_HOME/DesktopWidgets
MANIFEST_NAME=install-manifest.txt

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

die() { echo "install.sh: $*" >&2; exit 1; }

COMPONENTS=""
YES=0
AUTOSTART=0
while [ $# -gt 0 ]; do
    case $1 in
        --components) [ $# -ge 2 ] || die "--components needs a value"; COMPONENTS=$2; shift 2;;
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
case $PREFIX in ""|/|"$HOME") die "refusing to install into '$PREFIX'";; esac

# Existing folder: only upgrade one that we made (it has our manifest).
if [ -e "$PREFIX" ] && [ -n "$(ls -A "$PREFIX" 2>/dev/null)" ] && [ ! -f "$PREFIX/$MANIFEST_NAME" ]; then
    die "$PREFIX exists and is not a Desktop Widgets install; choose another --prefix."
fi

# ---- choose widgets -------------------------------------------------------
if [ -z "$COMPONENTS" ]; then
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

# ---- copy the bundle (re-running upgrades in place) -----------------------
# ${PREFIX:?} stops the script if the variable were ever empty, so no rm can reach "/".
if [ -f "$PREFIX/$MANIFEST_NAME" ]; then
    echo "Upgrading the existing install."
    rm -rf "${PREFIX:?}/_internal"
    for key in $KEYS; do rm -f "${PREFIX:?}/$(binary_of "$key")"; done
fi
mkdir -p "$PREFIX" "$PREFIX/icons" "$BIN_DIR" "$DATA_HOME/applications"
cp -R "$SELF_DIR/DesktopWidgets/." "$PREFIX/"
cp "$SELF_DIR/icons/"*.png "$PREFIX/icons/"
cp "$SELF_DIR/uninstall.sh" "$PREFIX/uninstall.sh"
chmod 755 "$PREFIX/uninstall.sh"
# Widgets that were not chosen lose their binary; the shared _internal stays.
for key in $KEYS; do
    case " $CHOSEN " in *" $key "*) ;; *) rm -f "${PREFIX:?}/$(binary_of "$key")";; esac
done

# ---- launchers, menu entries, autostart -----------------------------------
write_entry() { # $1 = file, $2 = key, $3 = exec path, $4 = extra line (may be empty)
    {
        echo "[Desktop Entry]"
        echo "Type=Application"
        echo "Name=Desktop Widgets: $(title_of "$2")"
        echo "Comment=$(title_of "$2") desktop widget"
        echo "Exec=\"$3\""
        echo "Icon=$PREFIX/icons/desktopwidgets-$2.png"
        echo "Terminal=false"
        echo "Categories=Utility;"
        [ -z "$4" ] || echo "$4"
    } > "$1"
}

MANIFEST=$PREFIX/$MANIFEST_NAME
{
    echo "version=1"
    echo "prefix=$PREFIX"
    echo "components=$CHOSEN"
} > "$MANIFEST"

for key in $CHOSEN; do
    launcher=$BIN_DIR/desktopwidgets-$key
    printf '#!/bin/sh\nexec "%s/%s" "$@"\n' "$PREFIX" "$(binary_of "$key")" > "$launcher"
    chmod 755 "$launcher"
    entry=$DATA_HOME/applications/desktopwidgets-$key.desktop
    write_entry "$entry" "$key" "$launcher" ""
    echo "file=$launcher" >> "$MANIFEST"
    echo "file=$entry" >> "$MANIFEST"
    if [ "$AUTOSTART" -eq 1 ]; then
        mkdir -p "$CONFIG_HOME/autostart"
        auto=$CONFIG_HOME/autostart/desktopwidgets-$key.desktop
        write_entry "$auto" "$key" "$PREFIX/$(binary_of "$key")" "X-GNOME-Autostart-enabled=true"
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
