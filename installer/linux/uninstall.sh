#!/bin/sh
# Removes what install.sh put in place. Never uses sudo.
# Usage: uninstall.sh [--prefix DIR] [--purge-data | --keep-data] [--help]
# POSIX sh (dash) on purpose: a fresh Linux machine has no Python yet.
set -eu

KEYS="todo usage desktop"
SELF_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
MANIFEST_NAME=install-manifest.txt

die() { echo "uninstall.sh: $*" >&2; exit 1; }

# ---- our own home folder ---------------------------------------------------
# The same checks as install.sh, with a clear message (findings B-07, B-11).
[ -n "${HOME:-}" ] || die "HOME is not set. Set HOME to your home folder first, then run this again."
case $HOME in
    /*) ;;
    *) die "HOME is not an absolute path. Set HOME to your home folder first, then run this again.";;
esac
case $HOME in
    *[\'\"\\\$\`%]*) die "your home folder may not contain quotes, backslash, dollar, backtick or percent signs";;
esac

# The XDG rules: a relative folder is ignored, and the apps ignore it (B-03).
case ${XDG_DATA_HOME:-} in
    /*) DATA_HOME=$XDG_DATA_HOME;;
    *) DATA_HOME=$HOME/.local/share;;
esac
case ${XDG_CONFIG_HOME:-} in
    /*) CONFIG_HOME=$XDG_CONFIG_HOME;;
    *) CONFIG_HOME=$HOME/.config;;
esac
case ${XDG_RUNTIME_DIR:-} in
    /*) RUNTIME_DIR=$XDG_RUNTIME_DIR;;
    *) RUNTIME_DIR="";;
esac
BIN_DIR=$HOME/.local/bin
CONFIG_DIR=$CONFIG_HOME/DesktopWidgets
SHARED_SETTINGS=$CONFIG_DIR/widget_background.json

# Names are fixed: key -> binary / data folder.
name_of() { case $1 in todo) echo TodoWidget;; usage) echo UsageMonitor;; desktop) echo DesktopMonitor;; esac; }

# ---- small helpers --------------------------------------------------------
# One value from the manifest, e.g. "components".
manifest_value() { # $1 = key
    sed -n "s/^$1=//p" "$MANIFEST" 2>/dev/null | head -n 1
}

# Does a file belong to the install in $PREFIX? A launcher or autostart file
# names our folder directly; a menu entry names our launcher, which must point
# at our folder. Files of another install are left alone (finding B-04).
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

# ---- stopping a running widget (finding B-05) ------------------------------
# A widget holds a lock file while it runs: $XDG_RUNTIME_DIR/desktopwidgets-<key>.lock,
# or the same name inside its data folder. Deleting the binary under a running
# process would leave it alive, still writing into the data folder, so we stop
# it first. One find call lists every open lock file, which is quick even on a
# busy machine.
lock_holders() { # -> "<pid> <lock file>" for every process that holds one open
    find /proc -maxdepth 3 -type l -lname '*desktopwidgets-*.lock' -printf '%p -> %l\n' 2>/dev/null |
    while IFS= read -r line; do
        fd_path=${line%% -> *}
        target=${line#* -> }
        pid=${fd_path#/proc/}
        pid=${pid%%/*}
        case $pid in ''|*[!0-9]*) continue;; esac
        # Only our two lock places, never a file that merely looks like one.
        case $target in
            "$RUNTIME_DIR"/desktopwidgets-*.lock) [ -n "$RUNTIME_DIR" ] || continue;;
            "$CONFIG_DIR"/*/desktopwidgets-*.lock) ;;
            *) continue;;
        esac
        printf '%s %s\n' "$pid" "$target"
    done | sort -u
}

# True only when this pid really runs our own binary from this install folder.
# A process that merely holds the lock file is not touched.
runs_our_binary() { # $1 = pid, $2 = binary path
    exe=$(readlink "/proc/$1/exe" 2>/dev/null) || exe=""
    case $exe in "$2"|"$2 (deleted)") return 0;; esac
    cmdline=$(tr '\0' ' ' < "/proc/$1/cmdline" 2>/dev/null) || cmdline=""
    case $cmdline in "$2 "*|*" $2 "*|*" $2 ") return 0;; esac
    return 1
}

stop_widgets() {
    lock_holders | while IFS=' ' read -r pid lock; do
        # The lock file name tells us which widget it is.
        key=${lock##*/}
        key=${key#desktopwidgets-}
        key=${key%.lock}
        case " $KEYS " in *" $key "*) ;; *) continue;; esac
        runs_our_binary "$pid" "$PREFIX/$(name_of "$key")" || continue
        kill -TERM "$pid" 2>/dev/null || true
        # Give it five seconds to close its window and save its data.
        tries=0
        while kill -0 "$pid" 2>/dev/null && [ "$tries" -lt 50 ]; do
            sleep 0.1
            tries=$((tries + 1))
        done
        if kill -0 "$pid" 2>/dev/null; then
            kill -KILL "$pid" 2>/dev/null || true
        fi
        echo "Stopped the running $(name_of "$key") (pid $pid)."
    done
}

# ---- main -----------------------------------------------------------------
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
        if [ -f "$SELF_DIR/$MANIFEST_NAME" ]; then PREFIX=$SELF_DIR; else PREFIX=$DATA_HOME/DesktopWidgets; fi
    fi
    case $PREFIX in /*) ;; *) die "the folder must be an absolute path";; esac
    case $PREFIX in ""|/|"$HOME"|"$HOME/"|"$DATA_HOME"|"$CONFIG_HOME") die "refusing to remove '$PREFIX'";; esac
    MANIFEST=$PREFIX/$MANIFEST_NAME
    [ -f "$MANIFEST" ] || die "$PREFIX is not a Desktop Widgets install (no $MANIFEST_NAME)."

    # The manifest names the folder it was written for; refuse a mismatch
    # rather than remove a tree it does not describe (finding B-10).
    manifest_folder=$(manifest_value folder)
    if [ -n "$manifest_folder" ] && [ "$manifest_folder" != "$PREFIX" ]; then
        die "the manifest says this install lives in '$manifest_folder', but $PREFIX was asked for."
    fi
    # Which widgets this install held; their data folders are the ones we may purge.
    COMPONENTS=$(manifest_value components)
    [ -n "$COMPONENTS" ] || COMPONENTS=$KEYS

    # Decide about user data first, while the program is still there.
    if [ "$DATA" = ask ]; then
        DATA=keep
        if [ -d "$CONFIG_DIR" ] && [ -t 0 ]; then
            printf 'Also delete the settings and data of the widgets you are removing in %s? [y/N] ' "$CONFIG_DIR"
            read -r answer || answer=""
            case $answer in [yY]*) DATA=purge;; esac
        fi
    fi

    # Stop the widgets before any file is removed, so nothing writes back into
    # the data folder after the purge.
    stop_widgets

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

    # Everything the manifest lists, plus our fixed names in case an older
    # manifest or a dropped component left something behind (findings B-02, B-10).
    # Decide about every file first and remove them after: a menu entry is only
    # ours while its launcher still points here, so the launcher must go last.
    remove_list=$PREFIX/.desktopwidgets-remove-list
    : > "$remove_list"
    {
        sed -n 's/^file=//p' "$MANIFEST"
        for key in $KEYS; do
            printf '%s\n' "$DATA_HOME/applications/desktopwidgets-$key.desktop"
            printf '%s\n' "$CONFIG_HOME/autostart/desktopwidgets-$key.desktop"
            printf '%s\n' "$BIN_DIR/desktopwidgets-$key"
        done
    } | while IFS= read -r file; do
        if entry_is_ours "$file"; then printf '%s\n' "$file" >> "$remove_list"; fi
    done
    while IFS= read -r file; do
        rm -f "$file"
    done < "$remove_list"
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DATA_HOME/applications" 2>/dev/null || true
    rm -rf "${PREFIX:?}"
    echo "Removed the program files, launchers, menu entries and autostart entries."

    if [ "$DATA" = purge ]; then
        # $CONFIG_DIR is shared by all three widgets, so delete only the data
        # folders of the widgets this install contained (finding B-01).
        for key in $(echo "$COMPONENTS" | tr ',' ' '); do
            folder=$CONFIG_DIR/$(name_of "$key")
            case $folder in
                "$CONFIG_DIR"/*) rm -rf "${folder:?}"; echo "Deleted $folder";;
            esac
        done
        # The shared card background belongs to every widget: its own question,
        # and it is kept unless the user says yes.
        if [ -f "$SHARED_SETTINGS" ]; then
            if [ -t 0 ]; then
                printf 'Also delete the shared card background setting %s? [y/N] ' "$SHARED_SETTINGS"
                read -r answer || answer=""
                case $answer in
                    [yY]*) rm -f "$SHARED_SETTINGS"; echo "Deleted $SHARED_SETTINGS";;
                    *) echo "Keeping $SHARED_SETTINGS";;
                esac
            else
                echo "Keeping the shared card background setting $SHARED_SETTINGS (run with a terminal to be asked)."
            fi
        fi
    else
        echo "Your data is still in $CONFIG_DIR"
    fi
}

# One line so the shell has read everything before this script deletes itself.
main "$@"; exit $?
