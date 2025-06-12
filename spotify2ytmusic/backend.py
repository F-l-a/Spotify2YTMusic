#!/usr/bin/env python3

import json
import sys
import os
import time
import re
from itertools import tee
from datetime import datetime  # Importa il modulo datetime
import signal

from ytmusicapi import YTMusic
from typing import Optional, Union, Iterator, Dict, List
from collections import namedtuple
from dataclasses import dataclass, field
from spotify2ytmusic.normalized_metadata_algorithm import *

numeroTotaleTracce = 0  # Variabile globale per il numero totale di tracce da copiare
numeroTracciaCorrente = 0 # Variabile globale per tenere traccia delle tracce processate
matchIncompleto_count = 0 #aggiunta per contare i match incompleti
duplicate_count = 0 #aggiunta per contare le tracce duplicate
error_count = 0 #aggiunta per contare le tracce non trovate
playlistSorgente = ""  # Variabile globale per la playlist sorgente
playlistDestinazione = ""  # Variabile globale per la playlist destinazione

def handle_termination(signum, frame):
    print(f"Program terminated with signal {signum}. Cleaning up...", file=sys.__stdout__) # Stampa direttamente su console
    chiudiFile()  # Chiude il file o esegue altre operazioni di cleanup
    sys.exit(0)  # Termina il programma

# Associa il gestore al segnale SIGINT (Ctrl+C)
signal.signal(signal.SIGINT, handle_termination)

# Associa il gestore al segnale SIGTERM (terminazione forzata)
signal.signal(signal.SIGTERM, handle_termination)


# AGGIUNTO PER LOG SU FILE
import csv
fileOutput = None  # Variabile globale per gestire il file CSV

def inizializzaFile(filename):
    global fileOutput
    if fileOutput is None:  # Evita di riaprire il file se già aperto
        try:
            # Apre il file in modalità append (per aggiungere nuove righe) e per scrivere come CSV
            fileOutput = open(filename, "a", newline='', encoding="utf-8")
            
            scriviFile(["Inizio Sessione", f"Numero totale di tracce: {numeroTotaleTracce}", f"Playlist Sorgente: {playlistSorgente}", f"Playlist Destinazione: {playlistDestinazione}"])
        except Exception as e:
            print(f"Errore nell'apertura del file: {e}")

def scriviFile(riga):
    if fileOutput:
        try:
            writer = csv.writer(fileOutput)
            if riga:  # Se la riga non è vuota, aggiungi il timestamp
                timestamp = datetime.now().strftime("%y-%m-%d %H:%M:%S")
                writer.writerow([timestamp] + riga)
            else:  # Se la riga è vuota, scrivi una riga vuota
                writer.writerow([])
        except Exception as e:
            print(f"Errore nella scrittura del file: {e}")

def chiudiFile():
    global fileOutput
    if fileOutput:
        try:
            
            scriviFile(["Fine Sessione", f"Numero di elementi processati: {numeroTracciaCorrente}", f"Numero di match incompleti: {matchIncompleto_count}", f"Numero di canzoni non trovate: {error_count}", f"Numero di tracce saltate perché duplicate: {duplicate_count}"])
            scriviFile([]) # Aggiunge una riga vuota alla fine del file per separare le sessioni
            scriviFile([])
            fileOutput.close()  # Chiude il file CSV
            fileOutput = None  # Resetta la variabile dopo la chiusura
        except Exception as e:
            print(f"Errore nella chiusura del file: {e}")
            raise e

# FINE AGGIUNTA FILE

SongInfo = namedtuple("SongInfo", ["title", "artist", "album"])


def get_ytmusic() -> YTMusic:
    """
    @@@
    """
    if not os.path.exists("oauth.json"):
        print("ERROR: No file 'oauth.json' exists in the current directory.")
        print("       Have you logged in to YTMusic?  Run 'ytmusicapi oauth' to login")
        sys.exit(1)

    try:
        return YTMusic("oauth.json")
    except json.decoder.JSONDecodeError as e:
        print(f"ERROR: JSON Decode error while trying start YTMusic: {e}")
        print("       This typically means a problem with a 'oauth.json' file.")
        print("       Have you logged in to YTMusic?  Run 'ytmusicapi oauth' to login")
        sys.exit(1)


def _ytmusic_create_playlist(
    yt: YTMusic, title: str, description: str, privacy_status: str = "PRIVATE"
) -> str:
    """Wrapper on ytmusic.create_playlist

    This wrapper does retries with back-off because sometimes YouTube Music will
    rate limit requests or otherwise fail.

    privacy_status can be: PRIVATE, PUBLIC, or UNLISTED
    """

    def _create(
        yt: YTMusic, title: str, description: str, privacy_status: str
    ) -> Union[str, dict]:
        exception_sleep = 5
        for _ in range(10):
            try:
                """Create a playlist on YTMusic, retrying if it fails."""
                id = yt.create_playlist(
                    title=title, description=description, privacy_status=privacy_status
                )
                return id
            except Exception as e:
                print(
                    f"ERROR: (Retrying create_playlist: {title}) {e}. waiting {exception_sleep} seconds"
                )
                time.sleep(exception_sleep)
                exception_sleep *= 2

        return {
            "s2yt error": 'ERROR: Could not create playlist "{title}" after multiple retries'
        }

    id = _create(yt, title, description, privacy_status)
    #  create_playlist returns a dict if there was an error
    if isinstance(id, dict):
        print(f"ERROR: Failed to create playlist (name: {title}): {id}")
        sys.exit(1)

    time.sleep(1)  # seems to be needed to avoid missing playlist ID error

    return id


def load_playlists_json(filename: str = "playlists.json", encoding: str = "utf-8"):
    """Load the `playlists.json` Spotify playlist file"""
    return json.load(open(filename, "r", encoding=encoding))


def create_playlist(pl_name: str, privacy_status: str = "PRIVATE") -> None:
    """Create a YTMusic playlist


    Args:
        `pl_name` (str): The name of the playlist to create. It should be different to "".

        `privacy_status` (str: PRIVATE, PUBLIC, UNLISTED) The privacy setting of created playlist.
    """
    yt = get_ytmusic()

    id = _ytmusic_create_playlist(
        yt, title=pl_name, description=pl_name, privacy_status=privacy_status
    )
    print(f"Playlist ID: {id}")


def iter_spotify_liked_albums(
    spotify_playlist_file: str = "playlists.json",
    spotify_encoding: str = "utf-8",
) -> Iterator[SongInfo]:
    """Songs from liked albums on Spotify."""
    spotify_pls = load_playlists_json(spotify_playlist_file, spotify_encoding)

    if "albums" not in spotify_pls:
        return None

    for album in [x["album"] for x in spotify_pls["albums"]]:
        for track in album["tracks"]["items"]:
            yield SongInfo(track["name"], track["artists"][0]["name"], album["name"])


def iter_spotify_playlist(
    src_pl_id: Optional[str] = None,
    spotify_playlist_file: str = "playlists.json",
    spotify_encoding: str = "utf-8",
    reverse_playlist: bool = True,
) -> Iterator[SongInfo]:
    """Songs from a specific album ("Liked Songs" if None)

    Args:
        `src_pl_id` (Optional[str], optional): The ID of the source playlist. Defaults to None.
        `spotify_playlist_file` (str, optional): The path to the playlists backup files. Defaults to "playlists.json".
        `spotify_encoding` (str, optional): Characters encoding. Defaults to "utf-8".
        `reverse_playlist` (bool, optional): Is the playlist reversed when loading?  Defaults to True.

    Yields:
        Iterator[SongInfo]: The song's information
    """
    spotify_pls = load_playlists_json(spotify_playlist_file, spotify_encoding)

    def find_spotify_playlist(spotify_pls: Dict, src_pl_id: Union[str, None]) -> Dict:
        """Return the spotify playlist that matches the `src_pl_id`.

        Args:
            `spotify_pls`: The playlist datastrcuture saved by spotify-backup.
            `src_pl_id`: The ID of a playlist to find, or None for the "Liked Songs" playlist.
        """
        for src_pl in spotify_pls["playlists"]:
            if src_pl_id is None and str(src_pl.get("name")) == "Liked Songs":
                return src_pl
            if src_pl_id is not None and str(src_pl.get("id")) == src_pl_id:
                return src_pl
        print(f"Could not find Spotify playlist {src_pl_id}")
        raise ValueError(f"Could not find Spotify playlist {src_pl_id}")

    src_pl = find_spotify_playlist(spotify_pls, src_pl_id)
    src_pl_name = src_pl["name"]

    print(f"SORGENTE: Spotify Playlist: {src_pl_name}")

    pl_tracks = src_pl["tracks"]
    if reverse_playlist:
        pl_tracks = reversed(pl_tracks)

    for src_track in pl_tracks:
        if src_track["track"] is None:
            print(
                f"WARNING: Spotify track seems to be malformed, Skipping.  Track: {src_track!r}"
            )
            continue

        try:
            src_album_name = src_track["track"]["album"]["name"]
            src_track_artist = src_track["track"]["artists"][0]["name"]
        except TypeError as e:
            print(f"ERROR: Spotify track seems to be malformed.  Track: {src_track!r}")
            raise e
        src_track_name = src_track["track"]["name"]

        yield SongInfo(src_track_name, src_track_artist, src_album_name)


def get_playlist_id_by_name(yt: YTMusic, title: str) -> Optional[str]:
    """Look up a YTMusic playlist ID by name.

    Args:
        `yt` (YTMusic): _description_
        `title` (str): _description_

    Returns:
        Optional[str]: The playlist ID or None if not found.
    """
    #  ytmusicapi seems to run into some situations where it gives a Traceback on listing playlists
    #  https://github.com/sigma67/ytmusicapi/issues/539
    try:
        playlists = yt.get_library_playlists(limit=5000)
    except KeyError as e:
        print("=" * 60)
        print(f"Attempting to look up playlist '{title}' failed with KeyError: {e}")
        print(
            "This is a bug in ytmusicapi that prevents 'copy_all_playlists' from working."
        )
        print(
            "You will need to manually copy playlists using s2yt_list_playlists and s2yt_copy_playlist"
        )
        print(
            "until this bug gets resolved.  Try `pip install --upgrade ytmusicapi` just to verify"
        )
        print("you have the latest version of that library.")
        print("=" * 60)
        raise

    for pl in playlists:
        if pl["title"] == title:
            return pl["playlistId"]

    return None


@dataclass
class ResearchDetails:
    query: Optional[str] = field(default=None)
    songs: Optional[List[Dict]] = field(default=None)
    suggestions: Optional[List[str]] = field(default=None)


def lookup_song(
    yt: YTMusic,
    track_name: str,
    artist_name: str,
    album_name,
    yt_search_algo: int,
    details: Optional[ResearchDetails] = None,
) -> dict:
    """Look up a song on YTMusic

    Given the Spotify track information, it does a lookup for the album by the same
    artist on YTMusic, then looks at the first 3 hits looking for a track with exactly
    the same name. In the event that it can't find that exact track, it then does
    a search of songs for the track name by the same artist and simply returns the
    first hit.

    The idea is that finding the album and artist and then looking for the exact track
    match will be more likely to be accurate than searching for the song and artist and
    relying on the YTMusic yt_search_algorithm to figure things out, especially for short tracks
    that might have many contradictory hits like "Survival by Yes".

    Args:
        `yt` (YTMusic)
        `track_name` (str): The name of the researched track
        `artist_name` (str): The name of the researched track's artist
        `album_name` (str): The name of the researched track's album
        `yt_search_algo` (int): 0 for exact matching, 1 for extended matching (Ricerca match perfetto Titolo-Artista-Album. Se non trova nelle prime 20 canzoni, ricerca match Titolo-Artista. Come ultima scelta, usa la prima canzone e lo riporta nel file output canzoniNO-MATCH.csv), 2 for approximate matching (search in videos), 3 for normalized metadata matching
        `details` (ResearchDetails): If specified, more information about the search and the response will be populated for use by the caller.

    Raises:
        ValueError: If no track is found, it returns an error

    Returns:
        dict: The infos of the researched song
    """

    """ NON USO LA RICERCA PER ALBUM PERCHé RITORNA LA VERSIONE VIDEO PER GLI ACCOUNT NON PREMIUM
    albums = yt.search(query=f"{album_name} by {artist_name}", filter="albums")
    for album in albums[:3]:
        # print(album)
        # print(f"ALBUM: {album['browseId']} - {album['title']} - {album['artists'][0]['name']}")

        try:
            for track in yt.get_album(album["browseId"])["tracks"]:
                if normalized_track_match(track_name, album_name, artist_name, track):
                    return track
            # print(f"{track['videoId']} - {track['title']} - {track['artists'][0]['name']}")
        except Exception as e:
            print(f"Unable to lookup album ({e}), continuing...")
    """
    global matchIncompleto_count  # Dichiara che stiamo usando la variabile globale

    query = f"{track_name} {artist_name}" #PRIMA C'ERA 'BY'
    if details:
        details.query = query
        details.suggestions = yt.get_search_suggestions(query=query)
    songs = yt.search(query=query, filter="songs")    

    match yt_search_algo:
        case 0:
            if details:
                details.songs = songs
            return songs[0]

        case 1:
            numeroCanzoniStampate = 0 #stampo solo le prime x canzoni, la probabilità che un possibile match sia più in basso è bassa
            for song in songs:
                if (
                    song["title"] == track_name
                    and song["artists"][0]["name"] == artist_name
                    and song["album"] is not None
                    and song["album"]["name"] == album_name
                ):
                    return song
                else:
                    if numeroCanzoniStampate >= 3:
                        continue
                    numeroCanzoniStampate += 1
                    print(f"\tNO-MATCH: {song['title']} - {song['artists'][0]['name']} - {song['album']['name'] if song['album'] is not None else "no-album"} - {song['videoId']}")
            
            #ripeto la ricerca senza considerare l'album (alcune canzoni non hanno un album).anche se il match non è perfetto, non ha senso loggare le canzoni trovate così
            print("\t-->Performing album-independent matching...")
            for song in songs:
                if (
                    song["title"] == track_name
                    and song["artists"][0]["name"] == artist_name
                ):
                    return song
                
            #se ancora non ho trovato nulla loggo e uso il primo risultato
            print(f"\t-->NOT FOUND. using first result: https://youtu.be/{songs[0]['videoId']}")

            #scrivo sul file di log le canzoni con match da controllare
            scriviFile(["Spotify", track_name, artist_name, album_name])
            scriviFile(["YouTubeMusic", songs[0]['title'], songs[0]['artists'][0]['name'], songs[0]['album']['name'] if songs[0]['album'] is not None else "no-album", f"https://youtu.be/{songs[0]['videoId']}"])
            scriviFile([])

            matchIncompleto_count += 1 #conto un match incompleto in più

            return songs[0]#aggiunto: se non trovo un match preciso, uso la prima canzone
            raise ValueError(
                f"Did not find {track_name} by {artist_name} from {album_name}. Added the first result of the list"
            )

        case 2:
            #  This would need to do fuzzy matching
            for song in songs:
                # Remove everything in brackets in the song title
                song_title_without_brackets = re.sub(r"[\[(].*?[])]", "", song["title"])
                if (
                    (
                        song_title_without_brackets == track_name
                        and song["album"]["name"] == album_name
                    )
                    or (song_title_without_brackets == track_name)
                    or (song_title_without_brackets in track_name)
                    or (track_name in song_title_without_brackets)
                ) and (
                    song["artists"][0]["name"] == artist_name
                    or artist_name in song["artists"][0]["name"]
                ):
                    return song

            # Finds approximate match
            # This tries to find a song anyway. Works when the song is not released as a music but a video.
            else:
                track_name = track_name.lower()
                first_song_title = songs[0]["title"].lower()
                if (
                    track_name not in first_song_title
                    or songs[0]["artists"][0]["name"] != artist_name
                ):  # If the first song is not the one we are looking for
                    print("Not found in songs, searching videos")
                    new_songs = yt.search(
                        query=f"{track_name} by {artist_name}", filter="videos"
                    )  # Search videos

                    # From here, we search for videos reposting the song. They often contain the name of it and the artist. Like with 'Nekfeu - Ecrire'.
                    for new_song in new_songs:
                        new_song_title = new_song[
                            "title"
                        ].lower()  # People sometimes mess up the capitalization in the title
                        if (
                            track_name in new_song_title
                            and artist_name in new_song_title
                        ) or (track_name in new_song_title):
                            print("Found a video")
                            return new_song
                    else:
                        # Basically we only get here if the song isn't present anywhere on YouTube
                        raise ValueError(
                            f"Did not find {track_name} by {artist_name} from {album_name}"
                        )
                else:
                    return songs[0]
            
        case 3:
            numeroCanzoniStampate = 0 #stampo solo le prime x canzoni, la probabilità che un possibile match sia più in basso è bassa
            for song in songs:
                if (normalized_track_match(track_name, album_name, artist_name, song)):
                    return song
                else:
                    if numeroCanzoniStampate >= 3:
                        continue
                    numeroCanzoniStampate += 1
                    print(f"\tNO-MATCH: {song['title']} - {song['artists'][0]['name']} - {song['album']['name'] if song['album'] is not None else "no-album"} - {song['videoId']}")
            
            #se ancora non ho trovato nulla loggo e uso il primo risultato
            print(f"\t-->NOT FOUND. using first result: https://youtu.be/{songs[0]['videoId']}")

            #scrivo sul file di log le canzoni con match da controllare
            scriviFile(["Spotify", track_name, artist_name, album_name])
            scriviFile(["YouTubeMusic", songs[0]['title'], songs[0]['artists'][0]['name'], songs[0]['album']['name'] if songs[0]['album'] is not None else "no-album", f"https://youtu.be/{songs[0]['videoId']}"])
            scriviFile([])

            matchIncompleto_count += 1 #conto un match incompleto in più

            return songs[0]#aggiunto: se non trovo un match preciso, uso la prima canzone

            raise ValueError(
                f"Did not find '{track_name} by {artist_name}' from {album_name}\n\n"
            )


def copier(
    src_tracks: Iterator[SongInfo],
    dst_pl_id: Optional[str] = None,
    dry_run: bool = False,
    track_sleep: float = 0.1,
    yt_search_algo: int = 3,
    *,
    yt: Optional[YTMusic] = None,
):
    """
    @@@
    """
    if yt is None:
        yt = get_ytmusic()

    if dst_pl_id is not None:
        try:
            yt_pl = yt.get_playlist(playlistId=dst_pl_id)
        except Exception as e:
            print(f"ERROR: Unable to find YTMusic playlist {dst_pl_id}: {e}")
            print(
                "       Make sure the YTMusic playlist ID is correct, it should be something like "
            )
            print("      'PL_DhcdsaJ7echjfdsaJFhdsWUd73HJFca'")
            sys.exit(1)
        print(f"DESTINAZIONE: Youtube Playlist: {yt_pl['title']}")

    tracks_added_set = set()
    global numeroTracciaCorrente  # Dichiara che stiamo usando la variabile globale
    numeroTracciaCorrente = 0  # Inizializza il contatore delle tracce processate
    src_tracks, src_tracks_copy = tee(src_tracks)  # Duplica l'iteratore perché non può essere consumato più volte
    global numeroTotaleTracce  # Dichiara che stiamo usando la variabile globale
    numeroTotaleTracce = sum(1 for _ in src_tracks_copy)  # Conta gli elementi consumando la copia
    print(f"Numero totale di tracce da copiare: {numeroTotaleTracce}")

    inizializzaFile("canzoniNO-MATCH.csv")#Aggiunto

    for src_track in src_tracks:
        print("======\n\n======")#aggiunto

        numeroTracciaCorrente += 1
        print(f"{numeroTracciaCorrente}/{numeroTotaleTracce}: {numeroTracciaCorrente/numeroTotaleTracce*100:.2f}%")

        # presente nella versione di FrederikBertelsen
        # TODO: REMOVE THIS
        #if src_track.title.strip() == "" or src_track.artist.strip() == "" or src_track.album.strip() == "":
        #    continue

        print(f"Spotify:   {src_track.title} - {src_track.artist} - {src_track.album}")

        try:
            dst_track = lookup_song(
                yt, src_track.title, src_track.artist, src_track.album, yt_search_algo
            )
        except Exception as e:
            print(f"ERROR: Unable to look up song on YTMusic: {e}")
            global error_count  # Dichiara che stiamo usando la variabile globale
            error_count += 1
            scriviFile(["ERROR: Not Found on Youtube", src_track.title, src_track.artist, src_track.album])  # Aggiunto per loggare le canzoni non trovate
            continue

        yt_artist_name = "<Unknown>"
        if "artists" in dst_track and len(dst_track["artists"]) > 0:
            yt_artist_name = dst_track["artists"][0]["name"]

        album_info = dst_track.get("album")  # Prende album, può essere None o un dizionario
        if album_info is None:
            album_str = "no-album"
        elif "name" in album_info:
            album_str = album_info["name"]
        else:
            album_str = str(album_info)  # Se non ha 'name', stampa tutto l'oggetto album

        print(f"Youtube: {dst_track['title']} - {yt_artist_name} - {album_str}")

        if dst_track["videoId"] in tracks_added_set:
            print("(DUPLICATE, this track has already been added)")
            global duplicate_count  # Dichiara che stiamo usando la variabile globale
            duplicate_count += 1
            scriviFile(["DUPLICATE (presente)(YTMusic)", dst_track['title'], yt_artist_name, album_str])  # Aggiunto per loggare le canzoni duplicate
            scriviFile(["DUPLICATE (saltata)(Spotify)", src_track.title, src_track.artist, src_track.album])
        tracks_added_set.add(dst_track["videoId"])

        if not dry_run:
            exception_sleep = 5
            for _ in range(10):
                try:
                    if dst_pl_id is not None:
                        yt.add_playlist_items(
                            playlistId=dst_pl_id,
                            videoIds=[dst_track["videoId"]],
                            duplicates=False,
                        )
                    else:
                        yt.rate_song(dst_track["videoId"], "LIKE")
                    break
                except Exception as e:
                    print(
                        f"ERROR: (Retrying add_playlist_items: {dst_pl_id} {dst_track['videoId']}) {e}. waiting {exception_sleep} seconds"
                    )
                    time.sleep(exception_sleep)
                    exception_sleep *= 1.2

        if track_sleep:
            time.sleep(track_sleep)

    print()
    print(
        f"Added {len(tracks_added_set)} tracks, encountered {duplicate_count} duplicates, {error_count} errors, {matchIncompleto_count} incomplete matches"
    )
    chiudiFile()#Aggiunto


def copy_playlist(
    spotify_playlist_id: str,
    ytmusic_playlist_id: str,
    spotify_playlists_encoding: str = "utf-8",
    dry_run: bool = False,
    track_sleep: float = 0.1,
    yt_search_algo: int = 3,
    reverse_playlist: bool = True,
    privacy_status: str = "PRIVATE",
):
    """
    Copy a Spotify playlist to a YTMusic playlist
    @@@
    """

    if spotify_playlist_id == "":
        print("ERROR: Campo sorgente non può essere vuoto")
        return

    print("Using search algo n°: ", yt_search_algo)
    yt = get_ytmusic()
    pl_name: str = ""
    pl_name_spotify: str = ""

    if ytmusic_playlist_id.startswith("+"): # aggiungo le canzoni a playlist esistente usando il nome. se non esiste, creo nuova playlist con quel nome
        pl_name = ytmusic_playlist_id[1:]

        ytmusic_playlist_id = get_playlist_id_by_name(yt, pl_name)
        print(f"Cerco nella libreria di youtube music la playlist '{pl_name}': id={ytmusic_playlist_id}")

    if ytmusic_playlist_id == "" or ytmusic_playlist_id is None: # creo nuova playlist con nome = nome della playlist spotify
        if pl_name == "":
            print("No playlist name or ID provided, creating playlist...")
            spotify_pls: dict = load_playlists_json()
            for pl in spotify_pls["playlists"]:
                if len(pl.keys()) > 3 and pl["id"] == spotify_playlist_id:
                    pl_name_spotify = pl["name"]
                    pl_name = pl_name_spotify

        if pl_name != "":
            ytmusic_playlist_id = _ytmusic_create_playlist(
                yt,
                title=pl_name,
                description=pl_name,
                privacy_status=privacy_status,
            )
            print(f"NOTE: Created playlist '{pl_name}' with ID: {ytmusic_playlist_id}")
        else:
            print(f"ERROR: Couldn't create the playlist because no name was provided or could not be found in the Spotify playlists.")
            return

        #  create_playlist returns a dict if there was an error
        if isinstance(ytmusic_playlist_id, dict):
            print(f"ERROR: Failed to create playlist: {ytmusic_playlist_id}")
            sys.exit(1)

    if ytmusic_playlist_id == "-": # passo None a funzione copier: non crea/aggiunge alla playlist, ma mette like ai brani, letti al contrario da spotify per inserirli in ordine cronologico
        ytmusic_playlist_id = None
        reverse_playlist = True
        print(f"NOTE: Metto like alle canzoni/video senza inserirle in una playlist. verranno aggiunte ai brani piaciuti di YouTube Music")

    if spotify_playlist_id == "-": # passo None a iteratore iter_spotify_playlist: usa i brani preferiti di spotify come sorgente, letti al contrario per inserirli in ordine cronologico
        spotify_playlist_id = None
        reverse_playlist = True # verificare?
        print(f"NOTE: Uso come sorgente i brani preferiti di Spotify")
    
    spotify_pls: dict = load_playlists_json()
    for pl in spotify_pls["playlists"]:
        if len(pl.keys()) > 3 and pl["id"] == spotify_playlist_id:
            pl_name_spotify = pl["name"]


    global playlistSorgente, playlistDestinazione  # Dichiara che stiamo usando le variabili globali
    playlistSorgente = f"{pl_name_spotify} - {'brani salvati Spotify' if spotify_playlist_id is None else spotify_playlist_id}"
    playlistDestinazione = f"{pl_name} - {'brani salvati YoutubeMusic' if ytmusic_playlist_id is None else ytmusic_playlist_id}"
    
    copier(
        iter_spotify_playlist(
            spotify_playlist_id,
            spotify_encoding=spotify_playlists_encoding,
            reverse_playlist=reverse_playlist,
        ),
        ytmusic_playlist_id,
        dry_run,
        track_sleep,
        yt_search_algo,
        yt=yt,
    )


def copy_all_playlists(
    track_sleep: float = 0.1,
    dry_run: bool = False,
    spotify_playlists_encoding: str = "utf-8",
    yt_search_algo: int = 3,
    reverse_playlist: bool = True,
    privacy_status: str = "PRIVATE",
):
    """
    Copy all Spotify playlists (except Liked Songs) to YTMusic playlists
    """
    spotify_pls = load_playlists_json()
    yt = get_ytmusic()

    for src_pl in spotify_pls["playlists"]:
        if str(src_pl.get("name")) == "Liked Songs":
            continue

        pl_name = src_pl["name"]
        if pl_name == "":
            pl_name = f"Unnamed Spotify Playlist {src_pl['id']}"

        dst_pl_id = get_playlist_id_by_name(yt, pl_name)
        print(f"Looking up playlist '{pl_name}': id={dst_pl_id}")
        if dst_pl_id is None:
            dst_pl_id = _ytmusic_create_playlist(
                yt, title=pl_name, description=pl_name, privacy_status=privacy_status
            )

            #  create_playlist returns a dict if there was an error
            if isinstance(dst_pl_id, dict):
                print(f"ERROR: Failed to create playlist: {dst_pl_id}")
                sys.exit(1)
            print(f"NOTE: Created playlist '{pl_name}' with ID: {dst_pl_id}")


        global playlistSorgente, playlistDestinazione  # Dichiara che stiamo usando le variabili globali
        playlistSorgente = f"{src_pl['name']} - {src_pl['id']}"  # Aggiunto per loggare la playlist sorgente
        playlistDestinazione = f"{pl_name} - {dst_pl_id}"  # Aggiunto per loggare la playlist destinazione

        copier(
            iter_spotify_playlist(
                src_pl["id"],
                spotify_encoding=spotify_playlists_encoding,
                reverse_playlist=reverse_playlist,
            ),
            dst_pl_id,
            dry_run,
            track_sleep,
            yt_search_algo,
        )
        print("\nPlaylist done!\n")

    print("All done!")
