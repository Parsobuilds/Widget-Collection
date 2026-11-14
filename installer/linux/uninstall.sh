#!/bin/sh
# Removes what install.sh put in place. Never uses sudo.
# Usage: uninstall.sh [--prefix DIR] [--purge-data | --keep-data] [--help]
set -eu

KEYS="todo usage desktop"
DATA_HOME=${XDG_DATA_HOME:-$HOME/.local/share}
CONFIG_HOME=${XDG_CONFIG_HOME:-$HOME/.config}
BIN_DIR=$HOME/.local/bin
SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

die() { echo "uninstall.sh: $*" >&2; exit 1; }

main() {
    PREFIX=""
    DATA=ask
    while [ $# -gt 0 ]; do
        case $1 in
            --prefix) [ $# -ge 2 ] || die "--prefix needs a folder"; PREFIX=$2; shift 2;;
            --purge-data) DATA=purge; shift;;
            --keep-data) DATA=keep; shift;;
            --help|-h) echo "Usage: uninstall.sh [--prefix DIR] [--purge-data | --keep-data]"; return 0;;
            *) die "unknown option: $1";;
        esac
    done
    [ "$(id -u)" -ne 0 ] || die "do not run this as root."
    if [ -z "$PREFIX" ]; then
        if [ -f "$SELF_DIR/install-manifest.txt" ]; then PREFIX=$SELF_DIR; else PREFIX=$DATA_HOME/DesktopWidgets; fi
    fi
    case $PREFIX in /*) ;; *) die "the folder must be an absolute path";; esac
    case $PREFIX in ""|/|"$HOME"|"$HOME/"|"$DATA_HOME"|"$CONFIG_HOME") die "refusing to remove '$PREFIX'";; esac
    [ -f "$PREFIX/install-manifest.txt" ] || die "$PREFIX is not a Desktop Widgets install (no install-manifest.txt)."

    # Decide about user data first, while the program is still there.
    CONFIG_DIR=$CONFIG_HOME/DesktopWidgets
    if [ "$DATA" = ask ]; then
        DATA=keep
        if [ -d "$CONFIG_DIR" ] && [ -t 0 ]; then
            printf 'Also delete your settings and data in %s? [y/N] ' "$CONFIG_DIR"
            read -r answer || answer=""
            case $answer in [yY]*) DATA=purge;; esac
        fi
    fi
    if [ "$DATA" = purge ] && [ -x "$PREFIX/UsageMonitor" ]; then
        # The Usage Monitor knows which keys it saved in the key store.
        if QT_QPA_PLATFORM=offscreen "$PREFIX/UsageMonitor" --purge-secrets >/dev/null 2>&1; then
            echo "Removed saved provider keys from the key store."
        else
            echo "Could not remove provider keys; delete them in your key store (Passwords and Keys)."
        fi
    elif [ "$DATA" = keep ]; then
        echo "Keeping your data and any provider keys in the key store."
    fi

    # Only our exact names; launchers are removed only if they point at this folder.
    for key in $KEYS; do
        launcher=$BIN_DIR/desktopwidgets-$key
        if [ -f "$launcher" ] && grep -qF "$PREFIX/" "$launcher"; then rm -f "${BIN_DIR:?}/desktopwidgets-$key"; fi
        rm -f "${DATA_HOME:?}/applications/desktopwidgets-$key.desktop"
        rm -f "${CONFIG_HOME:?}/autostart/desktopwidgets-$key.desktop"
    done
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA_HOME/applications" 2>/dev/null || true
    rm -rf "${PREFIX:?}"
    echo "Removed the program files, launchers, menu entries and autostart entries."

    if [ "$DATA" = purge ]; then
        case $CONFIG_DIR in */DesktopWidgets) rm -rf "${CONFIG_DIR:?}"; echo "Deleted $CONFIG_DIR";; esac
    else
        echo "Your data is still in $CONFIG_DIR"
    fi
}

# One line so the shell has read everything before this script deletes itself.
main "$@"; exit $?
