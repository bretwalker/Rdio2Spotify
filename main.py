import base64
import json
import re
import string
import sys
import unicodedata
from rauth import OAuth1Service, OAuth2Service

rdio_consumer_key=''
rdio_consumer_secret=''
spotify_client_id=''
spotify_client_secret=''

page_size = 50

def normalize_text(data):
    return re.sub(r'- .*$', '', re.sub(r'[\(\[][^)]*[\)\]]', '', unicodedata.normalize('NFKD', data.lower()).encode('ASCII', 'ignore'))).strip()
        
def get_sessions():
    rdio = OAuth1Service(
        name='rdio',
        consumer_key=rdio_consumer_key,
        consumer_secret=rdio_consumer_secret,
        request_token_url='http://api.rdio.com/oauth/request_token',
        access_token_url='http://api.rdio.com/oauth/access_token',
        authorize_url='https://www.rdio.com/oauth/authorize',
        base_url='http://api.rdio.com/1/')

    rdio_request_token, rdio_request_token_secret = rdio.get_request_token(params={'oauth_callback': 'oob'})
    rdio_authorize_url = rdio.get_authorize_url(rdio_request_token)

    print 'Visit this URL in your browser: ' + rdio_authorize_url
    rdio_pin = raw_input('Enter PIN from browser: ')

    rdio_session = rdio.get_auth_session(rdio_request_token,
                                       rdio_request_token_secret,
                                       method='POST',
                                       data={'oauth_verifier': rdio_pin})    

    spotify = OAuth2Service(
          name='spotify',
          client_id=spotify_client_id,
          client_secret=spotify_client_secret,
          authorize_url='https://accounts.spotify.com/authorize/',
          access_token_url='https://accounts.spotify.com/api/token',
          base_url='https://api.spotify.com',)

    params={'scope':'user-library-modify user-library-read playlist-read-private playlist-modify-public', 'response_type': 'code', 'redirect_uri': 'http://localhost'}
    spotify_authorize_url = spotify.get_authorize_url(**params)

    print 'Visit this URL in your browser: ' + spotify_authorize_url
    spotify_pin = raw_input('Enter code from URL: ')

    spotify_session = spotify.get_auth_session(method='POST',
                                               data={'code': spotify_pin, 
                                                     'grant_type': 'authorization_code',
                                                     'redirect_uri': 'http://localhost',},
                                               headers={'Authorization': 'Basic ' + base64.b64encode(spotify_client_id + ":" + spotify_client_secret)},
                                               decoder=json.loads)
                                                 
    return rdio_session, spotify_session

def search(track_to_match, spotify_session, album_ids, matched_tracks, unmatched_tracks, match_album=False):
    search_term = normalize_text(track_to_match['artist'] + ' ' + track_to_match['name'])
    search_results = spotify_session.get('/v1/search', params={'q': search_term, 'type': 'track', 'limit': 50})

    matched_track = None
    
    if search_results.status_code != 200:
        print search_results
        print search_results.text
        print search_results.json()
        unmatched_tracks.append(search_term)
        return matched_track, album_ids, matched_tracks, unmatched_tracks

    if search_results.json()['tracks']['items']:
        matched_track = search_results.json()['tracks']['items'][0]
    else:
        search_results = spotify_session.get('/v1/search', params={'q': normalize_text(track_to_match['name']), 'type': 'track', 'limit': 50})
    
    for search_result in search_results.json()['tracks']['items']:
        if (#"US" in search_result['album']['available_markets']
            #and 
            (normalize_text(track_to_match['album']) in normalize_text(search_result['album']['name'])
                or match_album==False)
            and normalize_text(search_result['name']) in normalize_text(track_to_match['name'])
            and normalize_text(search_result['artists'][0]['name']) in normalize_text(track_to_match['artist'])
            #and search_result['explicit'] == track_to_match['isExplicit']
            ):
            matched_track = search_result
        
        # try to group songs using same spotify album
        if (search_result['album']['name'].lower() not in album_ids
            or album_ids[search_result['album']['name'].lower()] == search_result['album']['id']):
            break

    if matched_track:
        matched_tracks.append(search_term)
    else:
        unmatched_tracks.append(search_term)
    
    return matched_track, album_ids, matched_tracks, unmatched_tracks
                   
def sync_collection_albums(rdio_session, spotify_session):
    print 'Syncing collection albums'

    albums = rdio_session.post('', data={'method': 'getAlbumsInCollection', 'count': page_size}, verify=True)

    if albums.status_code != 200:
        print albums.json()
        return
    
    matched_albums = []
    unmatched_albums = []        
    
    search_loop = 2
    keep_processing = True
    while keep_processing:
        if len(albums.json()['result']) < page_size:
            keep_processing = False

        for album in albums.json()['result']:
            sys.stdout.write('.')
            sys.stdout.flush()
            matched_album = None
            
            search_results = spotify_session.get('/v1/search', params={'q': normalize_text(album['artist'] + ' ' + album['name']), 'type': 'album', 'limit': 50})

            if search_results.json()['albums']['items']:
                matched_album = search_results.json()['albums']['items'][0]

            for search_result in search_results.json()['albums']['items']:
                if ('US' in search_result['available_markets']
                    and normalize_text(album['name']) in normalize_text(search_result['name'])
                    and search_result['album_type'] == 'album'):
                    matched_album = search_result
                    break
                    
            if matched_album:
                album_tracks = spotify_session.get('v1/albums/%s/tracks' % matched_album['id'])
                if album_tracks.status_code != 200:
                    unmatched_albums.append(album['artist'] + ' ' + album['name'])
                    print album_tracks.json()
                    continue
                
                track_ids = []
                for album_track in album_tracks.json()['items']:
                    track_ids.append(album_track['id'])
                
                if len(track_ids) > 0:
                    spotify_session.put('/v1/me/tracks?ids=%s' % ','.join(track_ids))
                
                matched_albums.append(album['artist'] + ' ' + album['name'])
            else:
                unmatched_albums.append(album['artist'] + ' ' + album['name'])
        
        albums = rdio_session.post('', data={'method': 'getAlbumsInCollection', 'count': page_size*search_loop}, verify=True)
        search_loop = search_loop + 1

    print ''
    print 'Matched albums: '
    print '\n'.join(matched_albums)
    print ''
    print 'Unmatched albums: '
    print '\n'.join(unmatched_albums)    

def sync_collection(rdio_session, spotify_session):
    print 'Syncing collection'
                                
    tracks = rdio_session.post('', data={'method': 'getTracksInCollection', 'count': page_size}, verify=True)

    if tracks.status_code != 200:
        print tracks.json()
        return

    matched_tracks = []
    unmatched_tracks = []
    
    search_loop = 2
    album_ids = {}
    keep_processing = True
    while keep_processing:
        if len(tracks.json()['result']) < page_size:
            keep_processing = False
            
        for track in tracks.json()['result']:
            matched_track, album_ids, matched_tracks, unmatched_tracks = search(track, spotify_session, album_ids, matched_tracks, unmatched_tracks, True)
            
            if matched_track:
                spotify_session.put('/v1/me/tracks', params={'ids': matched_track['id']})

            sys.stdout.write('.')
            sys.stdout.flush()
            
        tracks = rdio_session.post('', data={'method': 'getTracksInCollection', 'count': page_size, 'start': page_size * search_loop}, verify=True)
        search_loop = search_loop + 1
        
    print ''
    print 'Matched tracks: '
    print '\n'.join(matched_tracks)
    print ''
    print 'Unmatched tracks: '
    print '\n'.join(unmatched_tracks)

def sync_playlists(rdio_session, spotify_session):
    print 'Syncing playlists'
    
    rdio_playlists = rdio_session.post('', data={'method': 'getPlaylists', 'extras': 'tracks'}, verify=True)

    if rdio_playlists.status_code != 200:
        print rdio_playlists.json()
        return
        
    rdio_playlists = rdio_playlists.json()
    if 'result' not in rdio_playlists or 'owned' not in rdio_playlists['result'] or 'subscribed' not in rdio_playlists['result']:
        print 'No owned or subscribed playlists'
    else:
        spotify_id = spotify_session.get('/v1/me').json()['id']
        spotify_playlists = spotify_session.get('/v1/users/%s/playlists' % spotify_id)
        
        if spotify_playlists.status_code != 200:
            print spotify_playlists.json()
            return
        
        rdio_playlists_to_process = []
        if 'owned' in rdio_playlists['result']:
            rdio_playlists_to_process = rdio_playlists_to_process + rdio_playlists['result']['owned']
        if 'subscribed' in rdio_playlists['result']:
            rdio_playlists_to_process= rdio_playlists_to_process + rdio_playlists['result']['subscribed']
        
        spotify_playlists = spotify_playlists.json()
        for rdio_playlist in rdio_playlists_to_process:
            existing_spotify_playlist = None
            for spotify_playlist in spotify_playlists['items']:
                if spotify_playlist['name'] == rdio_playlist['name']:
                    existing_spotify_playlist = spotify_playlist
                    break
            
            if not existing_spotify_playlist:
                # set existing_spotify_playlist to a new playlist
                existing_spotify_playlist = spotify_session.post('/v1/users/%s/playlists' % spotify_id,
                                                                 json={'name': rdio_playlist['name']})
                                                                 
                if existing_spotify_playlist.status_code > 201:
                    print existing_spotify_playlist.json()
                    return
            
                existing_spotify_playlist = existing_spotify_playlist.json()
 
            matched_tracks = []
            unmatched_tracks = []
            album_ids = {}
            track_uris = []
            did_first_hundred = False
            
            last_track = rdio_playlist['tracks'][-1]
            for rdio_track in rdio_playlist['tracks']:
                matched_track, album_ids, matched_tracks, unmatched_tracks = search(rdio_track, spotify_session, album_ids, matched_tracks, unmatched_tracks)
                                                            
                if matched_track:
                    # can't really update playlists easily, so replace all contents with fist 100 songs, then keep appending
                    if not did_first_hundred:
                        track_uris.append(matched_track['uri'])
                    else:
                        spotify_session.post('/v1/users/%s/playlists/%s/tracks' % (spotify_id, existing_spotify_playlist['id']),
                                             params={'uris': [matched_track['uri']]})

                if not did_first_hundred:
                    if len(track_uris) == 100 or last_track['key'] == rdio_track['key']:
                        did_first_hundred = True
                        spotify_session.put('/v1/users/%s/playlists/%s/tracks' % (spotify_id, existing_spotify_playlist['id']),
                                            json={'uris': track_uris})

                sys.stdout.write('.')
                sys.stdout.flush()
            
            print ''
            print 'Matched tracks: '
            print '\n'.join(matched_tracks)
            print ''
            print 'Unmatched tracks: '
            print '\n'.join(unmatched_tracks)
            
                        
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    
    parser.add_argument('-t', action='store_const', dest='tracks',
                        const=True, help='Sync collection by tracks')

    parser.add_argument('-a', action='store_const', dest='albums',
                        const=True, help='Sync collection by full albums')
                        
    parser.add_argument('-p', action='store_const', dest='playlists',
                        const=True, help='Sync owned and subscribed playlists')                        
                                                                    
    results = parser.parse_args()
    
    if not results.tracks and not results.albums and not results.playlists:
        parser.print_help()
        sys.exit(1)
    else:
        rdio_session, spotify_session = get_sessions()
    
    if results.tracks:
        sync_collection(rdio_session, spotify_session)
    if results.albums:
        sync_collection_albums(rdio_session, spotify_session)
    if results.playlists:
        sync_playlists(rdio_session, spotify_session)
                        