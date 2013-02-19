import collections
import dircache
import hashlib
import httplib
import HydrusConstants as HC
import HydrusServer
import os
import Queue
import shutil
import sqlite3
import sys
import threading
import time
import traceback
import yaml
import wx

class FileDB():
    
    def _AddFile( self, c, service_id, account_id, file_dict ):
        
        hash = file_dict[ 'hash' ]
        
        hash_id = self._GetHashId( c, hash )
        
        now = int( time.time() )
        
        if c.execute( 'SELECT 1 FROM file_map WHERE service_id = ? AND hash_id = ?;', ( service_id, hash_id ) ).fetchone() is None or c.execute( 'SELECT 1 FROM deleted_files WHERE service_id = ? AND hash_id = ?;', ( service_id, hash_id ) ).fetchone() is None:
            
            size = file_dict[ 'size' ]
            mime = file_dict[ 'mime' ]
            
            if 'width' in file_dict: width = file_dict[ 'width' ]
            else: width = None
            
            if 'height' in file_dict: height = file_dict[ 'height' ]
            else: height = None
            
            if 'duration' in file_dict: duration = file_dict[ 'duration' ]
            else: duration = None
            
            if 'num_frames' in file_dict: num_frames = file_dict[ 'num_frames' ]
            else: num_frames = None
            
            if 'num_words' in file_dict: num_words = file_dict[ 'num_words' ]
            else: num_words = None
            
            options = self._GetOptions( c, service_id )
            
            max_storage = options[ 'max_storage' ]
            
            if max_storage is not None:
                
                # this is wrong! no service_id in files_info. need to cross with file_map or w/e
                ( current_storage, ) = c.execute( 'SELECT SUM( size ) FROM file_map, files_info USING ( hash_id ) WHERE service_id = ?;', ( service_id, ) ).fetchone()
                
                if current_storage + size > max_storage: raise HC.ForbiddenException( 'The service is full! It cannot take any more files!' )
                
            
            dest_path = HC.SERVER_FILES_DIR + os.path.sep + hash.encode( 'hex' )
            
            if not os.path.exists( dest_path ):
                
                file = file_dict[ 'file' ]
                
                with open( dest_path, 'wb' ) as f: f.write( file )
                
            
            if 'thumbnail' in file_dict:
                
                thumbnail_dest_path = HC.SERVER_THUMBNAILS_DIR + os.path.sep + hash.encode( 'hex' )
                
                if not os.path.exists( thumbnail_dest_path ):
                    
                    thumbnail = file_dict[ 'thumbnail' ]
                    
                    with open( thumbnail_dest_path, 'wb' ) as f: f.write( thumbnail )
                    
                
            
            if c.execute( 'SELECT 1 FROM files_info WHERE hash_id = ?;', ( hash_id, ) ).fetchone() is None: c.execute( 'INSERT OR IGNORE INTO files_info ( hash_id, size, mime, width, height, duration, num_frames, num_words ) VALUES ( ?, ?, ?, ?, ?, ?, ?, ? );', ( hash_id, size, mime, width, height, duration, num_frames, num_words ) )
            
            c.execute( 'INSERT OR IGNORE INTO file_map ( service_id, hash_id, account_id, timestamp ) VALUES ( ?, ?, ?, ? );', ( service_id, hash_id, account_id, now ) )
            
            if options[ 'log_uploader_ips' ]:
                
                ip = file_dict[ 'ip' ]
                
                c.execute( 'INSERT INTO ip_addresses ( service_id, hash_id, ip, timestamp ) VALUES ( ?, ?, ?, ? );', ( service_id, hash_id, ip, now ) )
                
            
        
    
    def _AddFilePetition( self, c, service_id, account_id, reason_id, hash_ids ):
        
        self._ApproveOptimisedFilePetition( c, service_id, account_id, hash_ids )
        
        valid_hash_ids = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM file_map WHERE service_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';', ( service_id, ) ) ]
        
        # this clears out any old reasons, if the user wants to overwrite them
        c.execute( 'DELETE FROM file_petitions WHERE service_id = ? AND account_id = ? AND hash_id IN ' + HC.SplayListForDB( valid_hash_ids ) + ';', ( service_id, account_id ) )
        
        c.executemany( 'INSERT OR IGNORE INTO file_petitions ( service_id, account_id, reason_id, hash_id ) VALUES ( ?, ?, ?, ? );', [ ( service_id, account_id, reason_id, hash_id ) for hash_id in valid_hash_ids ] )
        
    
    def _ApproveFilePetition( self, c, service_id, account_id, reason_id, hash_ids ):
        
        self._ApproveOptimisedFilePetition( c, service_id, account_id, hash_ids )
        
        valid_hash_ids = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM file_map WHERE service_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';', ( service_id, ) ) ]
        
        self._RewardFilePetitioners( c, service_id, valid_hash_ids, 1 )
        
        self._DeleteFiles( c, service_id, account_id, reason_id, valid_hash_ids )
        
    
    def _ApproveOptimisedFilePetition( self, c, service_id, account_id, hash_ids ):
        
        ( biggest_end, ) = c.execute( 'SELECT end FROM update_cache ORDER BY end DESC LIMIT 1;' ).fetchone()
        
        c.execute( 'DELETE FROM file_map WHERE service_id = ? AND account_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ' AND timestamp > ?;', ( service_id, account_id, biggest_end ) )
        
    
    def _DeleteFiles( self, c, service_id, admin_account_id, reason_id, hash_ids ):
        
        splayed_hash_ids = HC.SplayListForDB( hash_ids )
        
        affected_timestamps = [ timestamp for ( timestamp, ) in c.execute( 'SELECT DISTINCT timestamp FROM file_map WHERE service_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( service_id, ) ) ]
        
        c.execute( 'INSERT OR IGNORE INTO deleted_files ( service_id, hash_id, reason_id, account_id, admin_account_id, timestamp ) SELECT service_id, hash_id, ?, account_id, ?, ? FROM file_map WHERE service_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( reason_id, admin_account_id, int( time.time() ), service_id ) )
        
        c.execute( 'DELETE FROM file_map WHERE service_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( service_id, ) )
        c.execute( 'DELETE FROM file_petitions WHERE service_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( service_id, ) )
        
        self._RefreshUpdateCache( c, service_id, affected_timestamps )
        
    
    def _DenyFilePetition( self, c, service_id, hash_ids ):
        
        self._RewardFilePetitioners( c, service_id, hash_ids, -1 )
        
        c.execute( 'DELETE FROM file_petitions WHERE hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';' )
        
    
    def _GenerateFileUpdate( self, c, service_id, begin, end ):
        
        files_info = [ ( hash, size, mime, timestamp, width, height, duration, num_frames, num_words ) for ( hash, size, mime, timestamp, width, height, duration, num_frames, num_words ) in c.execute( 'SELECT hash, size, mime, timestamp, width, height, duration, num_frames, num_words FROM file_map, ( files_info, hashes USING ( hash_id ) ) USING ( hash_id ) WHERE service_id = ? AND timestamp BETWEEN ? AND ?;', ( service_id, begin, end ) ) ]
        
        deleted_hashes = [ hash for ( hash, ) in c.execute( 'SELECT hash FROM deleted_files, hashes USING ( hash_id ) WHERE service_id = ? AND timestamp BETWEEN ? AND ?;', ( service_id, begin, end ) ) ]
        
        news = c.execute( 'SELECT news, timestamp FROM news WHERE service_id = ? AND timestamp BETWEEN ? AND ?;', ( service_id, begin, end ) ).fetchall()
        
        return HC.HydrusUpdateFileRepository( files_info, deleted_hashes, news, begin, end )
        
    
    def _GenerateHashIdsEfficiently( self, c, hashes ):
        
        hashes_not_in_db = set( hashes )
        
        for i in range( 0, len( hashes ), 250 ): # there is a limit on the number of parameterised variables in sqlite, so only do a few at a time
            
            hashes_subset = hashes[ i : i + 250 ]
            
            hashes_not_in_db.difference_update( [ hash for ( hash, ) in c.execute( 'SELECT hash FROM hashes WHERE hash IN (' + ','.join( '?' * len( hashes_subset ) ) + ');', [ sqlite3.Binary( hash ) for hash in hashes_subset ] ) ] )
            
        
        if len( hashes_not_in_db ) > 0: c.executemany( 'INSERT INTO hashes ( hash ) VALUES( ? );', [ ( sqlite3.Binary( hash ), ) for hash in hashes_not_in_db ] )
        
    
    def _GetFile( self, hash ):
        
        try:
            
            with open( HC.SERVER_FILES_DIR + os.path.sep + hash.encode( 'hex' ), 'rb' ) as f: file = f.read()
            
        except: raise HC.NotFoundException( 'Could not find that file!' )
        
        return file
        
    
    def _GetFilePetition( self, c, service_id ):
        
        result = c.execute( 'SELECT DISTINCT account_id, reason_id FROM file_petitions WHERE service_id = ? ORDER BY RANDOM() LIMIT 1;', ( service_id, ) ).fetchone()
        
        if result is None: raise HC.NotFoundException( 'No petitions!' )
        
        ( account_id, reason_id ) = result
        
        petitioner_account_identifier = HC.AccountIdentifier( account_id = account_id )
        
        reason = self._GetReason( c, reason_id )
        
        hash_ids = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM file_petitions WHERE service_id = ? AND account_id = ? AND reason_id = ?;', ( service_id, account_id, reason_id ) ) ]
        
        hashes = self._GetHashes( c, hash_ids )
        
        return HC.ServerFilePetition( petitioner_account_identifier, reason, hashes )
        
    
    def _GetHash( self, c, hash_id ):
        
        result = c.execute( 'SELECT hash FROM hashes WHERE hash_id = ?;', ( hash_id, ) ).fetchone()
        
        if result is None: raise Exception( 'File hash error in database' )
        
        ( hash, ) = result
        
        return hash
        
    
    def _GetHashes( self, c, hash_ids ): return [ hash for ( hash, ) in c.execute( 'SELECT hash FROM hashes WHERE hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';' ) ]
    
    def _GetHashId( self, c, hash ):
        
        result = c.execute( 'SELECT hash_id FROM hashes WHERE hash = ?;', ( sqlite3.Binary( hash ), ) ).fetchone()
        
        if result is None:
            
            c.execute( 'INSERT INTO hashes ( hash ) VALUES ( ? );', ( sqlite3.Binary( hash ), ) )
            
            hash_id = c.lastrowid
            
            return hash_id
            
        else:
            
            ( hash_id, ) = result
            
            return hash_id
            
        
    
    def _GetHashIds( self, c, hashes ):
        
        hash_ids = []
        
        if type( hashes ) == type( set() ): hashes = list( hashes )
        
        for i in range( 0, len( hashes ), 250 ): # there is a limit on the number of parameterised variables in sqlite, so only do a few at a time
            
            hashes_subset = hashes[ i : i + 250 ]
            
            hash_ids.extend( [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM hashes WHERE hash IN (' + ','.join( '?' * len( hashes_subset ) ) + ');', [ sqlite3.Binary( hash ) for hash in hashes_subset ] ) ] )
            
        
        if len( hashes ) > len( hash_ids ):
            
            if len( set( hashes ) ) > len( hash_ids ):
                
                # must be some new hashes the db has not seen before, so let's generate them as appropriate
                
                self._GenerateHashIdsEfficiently( c, hashes )
                
                hash_ids = self._GetHashIds( c, hashes )
                
            
        
        return hash_ids
        
    
    def _GetHashIdsToHashes( self, c, hash_ids ): return { hash_id : hash for ( hash_id, hash ) in c.execute( 'SELECT hash_id, hash FROM hashes WHERE hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';' ) }
    
    def _GetIPTimestamp( self, c, service_id, hash_id ):
        
        result = c.execute( 'SELECT ip, timestamp FROM ip_addresses WHERE service_id = ? AND hash_id = ?;', ( service_id, hash_id ) ).fetchone()
        
        if result is None: raise HC.ForbiddenException( 'Did not find ip information for that hash.' )
        
        return result
        
    
    def _GetNumFilePetitions( self, c, service_id ): return c.execute( 'SELECT COUNT( * ) FROM ( SELECT DISTINCT account_id, reason_id FROM file_petitions WHERE service_id = ? );', ( service_id, ) ).fetchone()[0]
    
    def _GetThumbnail( self, hash ):
        
        try:
            
            with open( HC.SERVER_THUMBNAILS_DIR + os.path.sep + hash.encode( 'hex' ), 'rb' ) as f: thumbnail = f.read()
            
            return thumbnail
            
        except: raise HC.NotFoundException( 'Could not find that thumbnail!' )
        
    
    def _RewardFilePetitioners( self, c, service_id, hash_ids, multiplier ):
        
        scores = [ ( account_id, count * multiplier ) for ( account_id, count ) in c.execute( 'SELECT account_id, COUNT( * ) FROM file_petitions WHERE service_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ' GROUP BY account_id;', ( service_id, ) ) ]
        
        self._RewardAccounts( c, service_id, HC.SCORE_PETITION, scores )
        
    
class MessageDB():
    
    def _AddMessage( self, c, contact_key, message ):
        
        try: ( service_id, account_id ) = c.execute( 'SELECT service_id, account_id FROM contacts WHERE contact_key = ?;', ( sqlite3.Binary( contact_key ), ) ).fetchone()
        except: raise HC.ForbiddenException( 'Did not find that contact key for the message depot!' )
        
        message_key = os.urandom( 32 )
        
        c.execute( 'INSERT OR IGNORE INTO messages ( message_key, service_id, account_id, timestamp ) VALUES ( ?, ?, ?, ? );', ( sqlite3.Binary( message_key ), service_id, account_id, int( time.time() ) ) )
        
        dest_path = HC.SERVER_MESSAGES_DIR + os.path.sep + message_key.encode( 'hex' )
        
        with open( dest_path, 'wb' ) as f: f.write( message )
        
    
    def _AddStatuses( self, c, contact_key, statuses ):
        
        try: ( service_id, account_id ) = c.execute( 'SELECT service_id, account_id FROM contacts WHERE contact_key = ?;', ( sqlite3.Binary( contact_key ), ) ).fetchone()
        except: raise HC.ForbiddenException( 'Did not find that contact key for the message depot!' )
        
        now = int( time.time() )
        
        c.executemany( 'INSERT OR REPLACE INTO message_statuses ( status_key, service_id, account_id, status, timestamp ) VALUES ( ?, ?, ?, ?, ? );', [ ( sqlite3.Binary( status_key ), service_id, account_id, sqlite3.Binary( status ), now ) for ( status_key, status ) in statuses ] )
        
    
    def _CreateContact( self, c, service_id, account_id, public_key ):
        
        result = c.execute( 'SELECT public_key FROM contacts WHERE service_id = ? AND account_id = ?;', ( service_id, account_id ) ).fetchone()
        
        if result is not None:
            
            ( existing_public_key, ) = result
            
            if existing_public_key != public_key: raise HC.ForbiddenException( 'This account already has a public key!' )
            else: return
            
        
        contact_key = hashlib.sha256( public_key ).digest()
        
        c.execute( 'INSERT INTO contacts ( service_id, account_id, contact_key, public_key ) VALUES ( ?, ?, ?, ? );', ( service_id, account_id, sqlite3.Binary( contact_key ), public_key ) )
        
    
    def _GetMessage( self, c, service_id, account_id, message_key ):
        
        result = c.execute( 'SELECT 1 FROM messages WHERE service_id = ? AND account_id = ? AND message_key = ?;', ( service_id, account_id, sqlite3.Binary( message_key ) ) ).fetchone()
        
        if result is None: raise HC.ForbiddenException( 'Could not find that message key on message depot!' )
        
        try:
            
            with open( HC.SERVER_MESSAGES_DIR + os.path.sep + message_key.encode( 'hex' ), 'rb' ) as f: message = f.read()
            
        except: raise HC.NotFoundException( 'Could not find that message!' )
        
        return message
        
    
    def _GetMessageInfoSince( self, c, service_id, account_id, timestamp ):
        
        message_keys = [ message_key for ( message_key, ) in c.execute( 'SELECT message_key FROM messages WHERE service_id = ? AND account_id = ? AND timestamp > ? ORDER BY timestamp ASC;', ( service_id, account_id, timestamp ) ) ]
        
        statuses = [ status for ( status, ) in c.execute( 'SELECT status FROM message_statuses WHERE service_id = ? AND account_id = ? AND timestamp > ? ORDER BY timestamp ASC;', ( service_id, account_id, timestamp ) ) ]
        
        return ( message_keys, statuses )
        
    
    def _GetPublicKey( self, c, contact_key ):
        
        ( public_key, ) = c.execute( 'SELECT public_key FROM contacts WHERE contact_key = ?;', ( sqlite3.Binary( contact_key ), ) ).fetchone()
        
        return public_key
        
    
class TagDB():
    
    def _AddMappings( self, c, service_id, account_id, tag_id, hash_ids, overwrite_deleted ):
        
        if overwrite_deleted:
            
            splayed_hash_ids = HC.SplayListForDB( hash_ids )
            
            affected_timestamps = [ timestamp for ( timestamp, ) in c.execute( 'SELECT DISTINCT timestamp FROM deleted_mappings WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( service_id, tag_id ) ) ]
            
            c.execute( 'DELETE FROM deleted_mappings WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( service_id, tag_id ) )
            
            self._RefreshUpdateCache( c, service_id, affected_timestamps )
            
        else:
            
            already_deleted = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM deleted_mappings WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';', ( service_id, tag_id ) ) ]
            
            hash_ids = set( hash_ids ).difference( already_deleted )
            
        
        now = int( time.time() )
        
        c.executemany( 'INSERT OR IGNORE INTO mappings ( service_id, tag_id, hash_id, account_id, timestamp ) VALUES ( ?, ?, ?, ?, ? );', [ ( service_id, tag_id, hash_id, account_id, now ) for hash_id in hash_ids ] )
        
    
    def _AddMappingPetition( self, c, service_id, account_id, reason_id, tag_id, hash_ids ):
        
        self._ApproveOptimisedMappingPetition( c, service_id, account_id, tag_id, hash_ids )
        
        valid_hash_ids = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM mappings WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';', ( service_id, tag_id ) ) ]
        
        # this clears out any old reasons, if the user wants to overwrite them
        c.execute( 'DELETE FROM mapping_petitions WHERE service_id = ? AND account_id = ? AND tag_id = ? AND hash_id IN ' + HC.SplayListForDB( valid_hash_ids ) + ';', ( service_id, account_id, tag_id ) )
        
        c.executemany( 'INSERT OR IGNORE INTO mapping_petitions ( service_id, account_id, reason_id, tag_id, hash_id ) VALUES ( ?, ?, ?, ?, ? );', [ ( service_id, account_id, reason_id, tag_id, hash_id ) for hash_id in valid_hash_ids ] )
        
    
    def _ApproveMappingPetition( self, c, service_id, account_id, reason_id, tag_id, hash_ids ):
        
        self._ApproveOptimisedMappingPetition( c, service_id, account_id, tag_id, hash_ids )
        
        valid_hash_ids = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM mappings WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';', ( service_id, tag_id ) ) ]
        
        self._RewardMappingPetitioners( c, service_id, tag_id, valid_hash_ids, 1 )
        
        self._DeleteMappings( c, service_id, account_id, reason_id, tag_id, valid_hash_ids )
        
    
    def _ApproveOptimisedMappingPetition( self, c, service_id, account_id, tag_id, hash_ids ):
        
        ( biggest_end, ) = c.execute( 'SELECT end FROM update_cache WHERE service_id = ? ORDER BY end DESC LIMIT 1;', ( service_id, ) ).fetchone()
        
        c.execute( 'DELETE FROM mappings WHERE service_id = ? AND account_id = ? AND tag_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ' AND timestamp > ?;', ( service_id, account_id, biggest_end, tag_id ) )
        
    
    def _DeleteMappings( self, c, service_id, admin_account_id, reason_id, tag_id, hash_ids ):
        
        splayed_hash_ids = HC.SplayListForDB( hash_ids )
        
        affected_timestamps = [ timestamp for ( timestamp, ) in c.execute( 'SELECT DISTINCT timestamp FROM mappings WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( service_id, tag_id ) ) ]
        
        c.execute( 'INSERT OR IGNORE INTO deleted_mappings ( service_id, tag_id, hash_id, reason_id, account_id, admin_account_id, timestamp ) SELECT service_id, tag_id, hash_id, ?, account_id, ?, ? FROM mappings WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( reason_id, admin_account_id, int( time.time() ), service_id, tag_id ) )
        
        c.execute( 'DELETE FROM mappings WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( service_id, tag_id ) )
        c.execute( 'DELETE FROM mapping_petitions WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + splayed_hash_ids + ';', ( service_id, tag_id ) )
        
        self._RefreshUpdateCache( c, service_id, affected_timestamps )
        
    
    def _DenyMappingPetition( self, c, service_id, tag_id, hash_ids ):
        
        self._RewardMappingPetitioners( c, service_id, tag_id, hash_ids, -1 )
        
        c.execute( 'DELETE FROM mapping_petitions WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';', ( service_id, tag_id ) )
        
    
    def _GenerateMappingUpdate( self, c, service_id, begin, end ):
        
        hash_ids = set()
        
        mappings_dict = collections.defaultdict( list )
        
        for ( tag, hash_id ) in c.execute( 'SELECT tag, hash_id FROM tags, mappings USING ( tag_id ) WHERE service_id = ? AND timestamp BETWEEN ? AND ?;', ( service_id, begin, end ) ):
            
            mappings_dict[ tag ].append( hash_id )
            
            hash_ids.add( hash_id )
            
        
        mappings = mappings_dict.items()
        
        deleted_mappings_dict = collections.defaultdict( list )
        
        for ( tag, hash_id ) in c.execute( 'SELECT tag, hash_id FROM tags, deleted_mappings USING ( tag_id ) WHERE service_id = ? AND timestamp BETWEEN ? AND ?;', ( service_id, begin, end ) ):
            
            deleted_mappings_dict[ tag ].append( hash_id )
            
            hash_ids.add( hash_id )
            
        
        deleted_mappings = deleted_mappings_dict.items()
        
        hash_ids_to_hashes = self._GetHashIdsToHashes( c, hash_ids )
        
        news = c.execute( 'SELECT news, timestamp FROM news WHERE service_id = ? AND timestamp BETWEEN ? AND ?;', ( service_id, begin, end ) ).fetchall()
        
        return HC.HydrusUpdateTagRepository( mappings, deleted_mappings, hash_ids_to_hashes, news, begin, end )
        
    
    def _GenerateTagIdsEfficiently( self, c, tags ):
        
        tags_not_in_db = set( tags )
        
        for i in range( 0, len( tags ), 250 ): # there is a limit on the number of parameterised variables in sqlite, so only do a few at a time
            
            tags_subset = tags[ i : i + 250 ]
            
            tags_not_in_db.difference_update( [ tag for ( tag, ) in c.execute( 'SELECT tag FROM tags WHERE tag IN (' + ','.join( '?' * len( tags_subset ) ) + ');', [ tag for tag in tags_subset ] ) ] )
            
        
        if len( tags_not_in_db ) > 0: c.executemany( 'INSERT INTO tags ( tag ) VALUES( ? );', [ ( tag, ) for tag in tags_not_in_db ] )
        
    
    def _GetMappingPetition( self, c, service_id ):
        
        result = c.execute( 'SELECT DISTINCT account_id, reason_id, tag_id FROM mapping_petitions WHERE service_id = ? ORDER BY RANDOM() LIMIT 1;', ( service_id, ) ).fetchone()
        
        if result is None: raise HC.NotFoundException( 'No petitions!' )
        
        ( account_id, reason_id, tag_id ) = result
        
        petitioner_account_identifier = HC.AccountIdentifier( account_id = account_id )
        
        reason = self._GetReason( c, reason_id )
        
        tag = self._GetTag( c, tag_id )
        
        hash_ids = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM mapping_petitions WHERE service_id = ? AND account_id = ? AND reason_id = ? AND tag_id = ?;', ( service_id, account_id, reason_id, tag_id ) ) ]
        
        hashes = self._GetHashes( c, hash_ids )
        
        return HC.ServerMappingPetition( petitioner_account_identifier, reason, tag, hashes )
        
    
    def _GetNumMappingPetitions( self, c, service_id ): return c.execute( 'SELECT COUNT( * ) FROM ( SELECT DISTINCT account_id, reason_id, tag_id FROM mapping_petitions WHERE service_id = ? );', ( service_id, ) ).fetchone()[0]
    
    def _GetTag( self, c, tag_id ):
        
        result = c.execute( 'SELECT tag FROM tags WHERE tag_id = ?;', ( tag_id, ) ).fetchone()
        
        if result is None: raise Exception( 'Tag error in database' )
        
        ( tag, ) = result
        
        return tag
        
    
    def _GetTagId( self, c, tag ):
        
        tag = HC.CleanTag( tag )
        
        if tag == '': raise ForbiddenException( 'Tag of zero length!' )
        
        result = c.execute( 'SELECT tag_id FROM tags WHERE tag = ?;', ( tag, ) ).fetchone()
        
        if result is None:
            
            c.execute( 'INSERT INTO tags ( tag ) VALUES ( ? );', ( tag, ) )
            
            tag_id = c.lastrowid
            
            return tag_id
            
        else:
            
            ( tag_id, ) = result
            
            return tag_id
            
        
    
    def _RewardMappingPetitioners( self, c, service_id, tag_id, hash_ids, multiplier ):
        
        scores = [ ( account_id, count * multiplier ) for ( account_id, count ) in c.execute( 'SELECT account_id, COUNT( * ) FROM mapping_petitions WHERE service_id = ? AND tag_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ' GROUP BY account_id;', ( service_id, tag_id ) ) ]
        
        self._RewardAccounts( c, service_id, HC.SCORE_PETITION, scores )
        
    
class RatingDB():
    
    def _GenerateRatingUpdate( self, c, service_id, begin, end ):
        
        ratings_info = c.execute( 'SELECT hash, hash_id, score, count, new_timestamp, current_timestamp FROM aggregate_ratings, hashes USING ( hash_id ) WHERE service_id = ? AND ( new_timestamp BETWEEN ? AND ? OR current_timestamp BETWEEN ? AND ? );', ( service_id, begin, end, begin, end ) ).fetchall()
        
        current_timestamps = { rating_info[5] for rating_info in ratings_info }
        
        hash_ids = [ rating_info[1] for rating_info in ratings_info ]
        
        c.execute( 'UPDATE aggregate_ratings SET current_timestamp = new_timestamp AND new_timestamp = 0 WHERE service_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';', ( service_id, ) )
        
        c.executemany( 'UPDATE updates SET dirty = ? WHERE service_id = ? AND ? BETWEEN begin AND end;', [ ( True, service_id, current_timestamp ) for current_timestamp in current_timestamps if current_timestamp != 0 ] )
        
        ratings = [ ( hash, score, count ) for ( hash, hash_id, score, count, new_timestamp, current_timestamp ) in ratings_info ]
        
        news = c.execute( 'SELECT news, timestamp FROM news WHERE service_id = ? AND timestamp BETWEEN ? AND ?;', ( service_id, begin, end ) ).fetchall()
        
        return HC.HydrusUpdateRatingsRepository( ratings, news, begin, end )
        
    
    def _UpdateRatings( self, c, service_id, account_id, ratings ):
        
        hashes = [ rating[0] for rating in ratings ]
        
        hashes_to_hash_ids = self._GetHashesToHashIds( c, hashes )
        
        valued_ratings = [ ( hash, rating ) for ( hash, rating ) in ratings if rating is not None ]
        null_ratings = [ hash for ( hash, rating ) in ratings if rating is None ]
        
        c.executemany( 'REPLACE INTO ratings ( service_id, account_id, hash_id, rating ) VALUES ( ?, ?, ?, ? );', [ ( service_id, account_id, hashes_to_hash_ids[ hash ], rating ) for ( hash, rating ) in valued_ratings ] )
        c.executemany( 'DELETE FROM ratings WHERE service_id = ? AND account_id = ? AND hash_id = ?;', [ ( service_id, account_id, hashes_to_hash_ids[ hash ] ) for hash in null_ratings ] )
        
        hash_ids = set( hashes_to_hash_ids.values() )
        
        aggregates = c.execute( 'SELECT hash_id, SUM( rating ), COUNT( * ) FROM ratings WHERE service_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ' GROUP BY hash_id;' )
        
        missed_aggregate_hash_ids = hash_ids.difference( aggregate[0] for aggregate in aggregates )
        
        aggregates.extend( [ ( hash_id, 0.0, 0 ) for hash_id in missed_aggregate_hash_ids ] )        
        
        hash_ids_to_new_timestamps = { hash_id : new_timestamp for ( hash_id, new_timestamp ) in c.execute( 'SELECT hash_id, new_timestamp FROM aggregate_ratings WHERE service_id = ? AND hash_id IN ' + HC.SplayListForDB( hash_ids ) + ';', ( service_id, ) ) }
        
        now = int( time.time() )
        
        for ( hash_id, total_score, count ) in aggregates:
            
            score = float( total_score ) / float( count )
            
            if hash_id not in hash_ids_to_new_timestamps or hash_ids_to_new_timestamps[ hash_id ] == 0:
                
                new_timestamp = now + ( count * HC.UPDATE_DURATION / 10 )
                
            else:
                
                new_timestamp = max( now, hash_ids_to_new_timestamps[ hash_id ] - HC.UPDATE_DURATION )
                
            
            if hash_id not in hash_ids_to_new_timestamps: c.execute( 'INSERT INTO aggregate_ratings ( service_id, hash_id, score, count, new_timestamp, current_timestamp ) VALUES ( ?, ?, ?, ?, ?, ? );', ( service_id, hash_id, score, new_timestamp, 0 ) )
            elif new_timestamp != hash_ids_to_new_timestamps[ hash_id ]: c.execute( 'UPDATE aggregate_ratings SET new_timestamp = ? WHERE service_id = ? AND hash_id = ?;', ( new_timestamp, service_id, hash_id ) )
            
        
    
class ServiceDB( FileDB, MessageDB, TagDB ):
    
    def _AccountTypeExists( self, c, service_id, title ): return c.execute( 'SELECT 1 FROM account_types, account_type_map USING ( account_type_id ) WHERE service_id = ? AND title = ?;', ( service_id, title ) ).fetchone() is not None
    
    def _AddNews( self, c, service_id, news ): c.execute( 'INSERT INTO news ( service_id, news, timestamp ) VALUES ( ?, ?, ? );', ( service_id, news, int( time.time() ) ) )
    
    def _AddToExpires( self, c, service_id, account_ids, increase ): c.execute( 'UPDATE account_map SET expires = expires + ? WHERE service_id = ? AND account_id IN ' + HC.SplayListForDB( account_ids ) + ';', ( increase, service_id ) )
    
    def _Ban( self, c, service_id, action, admin_account_id, subject_account_ids, reason_id, expiration = None ):
        
        splayed_subject_account_ids = HC.SplayListForDB( subject_account_ids )
        
        now = int( time.time() )
        
        if expiration is not None: expires = expiration + now
        else: expires = None
        
        c.executemany( 'INSERT OR IGNORE INTO bans ( service_id, account_id, admin_account_id, reason_id, created, expires ) VALUES ( ?, ?, ?, ?, ? );', [ ( service_id, subject_account_id, admin_account_id, reason_id, now, expires ) for subject_account_id in subject_account_ids ] )
        
        c.execute( 'DELETE FROM file_petitions WHERE service_id = ? AND account_id IN ' + splayed_subject_account_ids + ';', ( service_id, ) )
        
        c.execute( 'DELETE FROM mapping_petitions WHERE service_id = ? AND account_id IN ' + splayed_subject_account_ids + ';', ( service_id, ) )
        
        if action == HC.SUPERBAN:
            
            hash_ids = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM files_info WHERE service_id = ? AND account_id IN ' + splayed_subject_account_ids + ';', ( service_id, ) ) ]
            
            if len( hash_ids ) > 0: self._DeleteLocalFiles( c, admin_account_id, reason_id, hash_ids )
            
            mappings_dict = HC.BuildKeyToListDict( c.execute( 'SELECT tag_id, hash_id FROM mappings WHERE service_id = ? AND account_id IN ' + splayed_subject_keys_ids + ';', ( service_id, ) ) )
            
            if len( mappings_dict ) > 0:
                
                for ( tag_id, hash_ids ) in mappings_dict.items(): self._DeleteMappings( c, admin_account_id, reason_id, tag_id, hash_ids )
                
            
        
    
    def _ChangeAccountType( self, c, service_id, account_ids, account_type_id ):
        
        splayed_account_ids = HC.SplayListForDB( account_ids )
        
        c.execute( 'UPDATE account_map SET account_type_id = ? WHERE service_id = ? AND account_id IN ' + splayed_account_ids + ';', ( account_type_id, service_id ) )
        
    
    def _CheckDataUsage( self, c ):
        
        ( version_year, version_month ) = c.execute( 'SELECT year, month FROM version;' ).fetchone()
        
        current_time_struct = time.gmtime()
        
        ( current_year, current_month ) = ( current_time_struct.tm_year, current_time_struct.tm_mon )
        
        if version_year != current_year or version_month != current_month:
            
            c.execute( 'UPDATE version SET year = ?, month = ?;', ( current_year, current_month ) )
            
            c.execute( 'UPDATE account_map SET used_bytes = ?, used_requests = ?;', ( 0, 0 ) )
            
        
    
    def _CheckMonthlyData( self, c ):
        
        service_identifiers = self._GetServiceIdentifiers( c )
        
        running_total = 0
        
        self._services_over_monthly_data = set()
        
        for service_identifier in service_identifiers:
            
            service_id = self._GetServiceId( c, service_identifier )
            
            options = self._GetOptions( c, service_id )
            
            service_type = service_identifier.GetType()
            
            if service_type != HC.SERVER_ADMIN:
                
                ( total_used_bytes, ) = c.execute( 'SELECT SUM( used_bytes ) FROM account_map WHERE service_id = ?;', ( service_id, ) ).fetchone()
                
                if total_used_bytes is None: total_used_bytes = 0
                
                running_total += total_used_bytes
                
                if 'max_monthly_data' in options:
                    
                    max_monthly_data = options[ 'max_monthly_data' ]
                    
                    if max_monthly_data is not None and total_used_bytes > max_monthly_data: self._services_over_monthly_data.add( service_identifier )
                    
                
            
        
        # have to do this after
        
        server_admin_options = self._GetOptions( c, self._server_admin_id )
        
        self._over_monthly_data = False
        
        if 'max_monthly_data' in server_admin_options:
            
            max_monthly_data = server_admin_options[ 'max_monthly_data' ]
            
            if max_monthly_data is not None and running_total > max_monthly_data: self._over_monthly_data = True
            
        
    
    def _CleanUpdate( self, c, service_id, begin, end ):
        
        service_type = self._GetServiceType( c, service_id )
        
        if service_type == HC.FILE_REPOSITORY: clean_update = self._GenerateFileUpdate( c, service_id, begin, end )
        elif service_type == HC.TAG_REPOSITORY: clean_update = self._GenerateMappingUpdate( c, service_id, begin, end )
        
        ( update_key, ) = c.execute( 'SELECT update_key FROM update_cache WHERE service_id = ? AND begin = ?;', ( service_id, begin ) ).fetchone()
        
        with open( HC.SERVER_UPDATES_DIR + os.path.sep + update_key, 'wb' ) as f: f.write( yaml.safe_dump( clean_update ) )
        
        c.execute( 'UPDATE update_cache SET dirty = ? WHERE service_id = ? AND begin = ?;', ( False, service_id, begin ) )
        
    
    def _ClearBans( self, c ):
        
        now = int( time.time() )
        
        c.execute( 'DELETE FROM bans WHERE expires < ?;', ( now, ) )
        
    
    def _CreateUpdate( self, c, service_id, begin, end ):
        
        service_type = self._GetServiceType( c, service_id )
        
        if service_type == HC.FILE_REPOSITORY: update = self._GenerateFileUpdate( c, service_id, begin, end )
        elif service_type == HC.TAG_REPOSITORY: update = self._GenerateMappingUpdate( c, service_id, begin, end )
        
        update_key_bytes = os.urandom( 32 )
        
        update_key = update_key_bytes.encode( 'hex' )
        
        with open( HC.SERVER_UPDATES_DIR + os.path.sep + update_key, 'wb' ) as f: f.write( yaml.safe_dump( update ) )
        
        c.execute( 'INSERT OR REPLACE INTO update_cache ( service_id, begin, end, update_key, dirty ) VALUES ( ?, ?, ?, ?, ? );', ( service_id, begin, end, update_key, False ) )
        
    
    def _DeleteOrphans( self, c ):
        
        # files
        
        deletees = [ hash_id for ( hash_id, ) in c.execute( 'SELECT DISTINCT hash_id FROM files_info EXCEPT SELECT DISTINCT hash_id FROM file_map;' ) ]
        
        if len( deletees ) > 0:
            
            deletee_hashes = set( self._GetHashes( c, deletees ) )
            
            local_files_hashes = { hash.decode( 'hex' ) for hash in dircache.listdir( HC.SERVER_FILES_DIR ) }
            thumbnails_hashes = { hash.decode( 'hex' ) for hash in dircache.listdir( HC.SERVER_THUMBNAILS_DIR ) }
            
            for hash in local_files_hashes & deletee_hashes: os.remove( HC.SERVER_FILES_DIR + os.path.sep + hash.encode( 'hex' ) )
            for hash in thumbnails_hashes & deletee_hashes: os.remove( HC.SERVER_THUMBNAILS_DIR + os.path.sep + hash.encode( 'hex' ) )
            
            c.execute( 'DELETE FROM files_info WHERE hash_id IN ' + HC.SplayListForDB( deletees ) + ';' )
            
        
        # messages
        
        required_message_keys = { message_key for ( message_key, ) in c.execute( 'SELECT DISTINCT message_key FROM messages;' ) }
        
        existing_message_keys = { key.decode( 'hex' ) for key in dircache.listdir( HC.SERVER_MESSAGES_DIR ) }
        
        deletees = existing_message_keys - required_message_keys
        
        for message_key in deletees: os.remove( HC.SERVER_MESSAGES_DIR + os.path.sep + message_key.encode( 'hex' ) )
        
    
    def _GenerateAccessKeys( self, c, service_id, num, account_type_id, expiration ):
        
        access_keys = [ os.urandom( HC.HYDRUS_KEY_LENGTH ) for i in range( num ) ]
        
        c.executemany( 'INSERT INTO accounts ( access_key ) VALUES ( ? );', [ ( sqlite3.Binary( hashlib.sha256( access_key ).digest() ), ) for access_key in access_keys ] )
        
        account_ids = self._GetAccountIds( c, access_keys )
        
        now = int( time.time() )
        
        if expiration is not None: expires = expiration + int( time.time() )
        else: expires = None
        
        c.executemany( 'INSERT INTO account_map ( service_id, account_id, account_type_id, created, expires, used_bytes, used_requests ) VALUES ( ?, ?, ?, ?, ?, ?, ? );', [ ( service_id, account_id, account_type_id, now, expires, 0, 0 ) for account_id in account_ids ] )
        
        return access_keys
        
    
    def _FlushRequestsMade( self, c, all_services_requests ):
        
        all_services_request_dict = HC.BuildKeyToListDict( [ ( service_identifier, ( account, num_bytes ) ) for ( service_identifier, account, num_bytes ) in all_services_requests ] )
        
        for ( service_identifier, requests ) in all_services_request_dict.items():
            
            service_id = self._GetServiceId( c, service_identifier )
            
            requests_dict = HC.BuildKeyToListDict( requests )
            
            c.executemany( 'UPDATE account_map SET used_bytes = used_bytes + ?, used_requests = used_requests + ? WHERE service_id = ? AND account_id = ?;', [ ( sum( num_bytes_list ), len( num_bytes_list ), service_id, account.GetAccountId() ) for ( account, num_bytes_list ) in requests_dict.items() ] )
            
        
    
    def _GetAccount( self, c, service_id, account_identifier ):
        
        if account_identifier.HasAccessKey():
            
            access_key = account_identifier.GetAccessKey()
            
            try: ( account_id, account_type, created, expires, used_bytes, used_requests ) = c.execute( 'SELECT account_id, account_type, created, expires, used_bytes, used_requests FROM account_types, ( accounts, account_map USING ( account_id ) ) USING ( account_type_id ) WHERE service_id = ? AND access_key = ?;', ( service_id, sqlite3.Binary( hashlib.sha256( access_key ).digest() ) ) ).fetchone()
            except: raise HC.ForbiddenException( 'The service could not find that account in its database.' )
            
        elif account_identifier.HasMapping():
            
            try:
                
                ( tag, hash ) = account_identifier.GetMapping()
                
                tag_id = self._GetTagId( c, tag )
                hash_id = self._GetHashId( c, hash )
                
            except: raise HC.ForbiddenException( 'The service could not find that mapping in its database.' )
            
            try: ( account_id, account_type, created, expires, used_bytes, used_requests ) = c.execute( 'SELECT account_id, account_type, created, expires, used_bytes, used_requests FROM account_types, ( accounts, ( account_map, mappings USING ( service_id, account_id ) ) USING ( account_id ) ) USING ( account_type_id ) WHERE service_id = ? AND tag_id = ? AND hash_id = ?;', ( service_id, tag_id, hash_id ) ).fetchone()
            except: raise HC.ForbiddenException( 'The service could not find that account in its database.' )
            
        elif account_identifier.HasHash():
            
            try: hash_id = self._GetHashId( c, account_identifier.GetHash() )
            except: raise HC.ForbiddenException( 'The service could not find that hash in its database.' )
            
            try:
                
                result = c.execute( 'SELECT account_id, account_type, created, expires, used_bytes, used_requests FROM account_types, ( accounts, ( account_map, file_map USING ( service_id, account_id ) ) USING ( account_id ) ) USING ( account_type_id ) WHERE service_id = ? AND hash_id = ?;', ( service_id, hash_id ) ).fetchone()
                
                if result is None: result = c.execute( 'SELECT account_id, account_type, created, expires, used_bytes, used_requests FROM account_types, ( accounts, ( account_map, deleted_files USING ( service_id, account_id ) ) USING ( account_id ) ) USING ( account_type_id ) WHERE service_id = ? AND hash_id = ?;', ( service_id, hash_id ) ).fetchone()
                
                ( account_id, account_type, created, expires, used_bytes, used_requests ) = result
                
            except: raise HC.ForbiddenException( 'The service could not find that account in its database.' )
            
        elif account_identifier.HasAccountId():
            
            try: ( account_id, account_type, created, expires, used_bytes, used_requests ) = c.execute( 'SELECT account_id, account_type, created, expires, used_bytes, used_requests FROM account_types, account_map USING ( account_type_id ) WHERE service_id = ? AND account_id = ?;', ( service_id, account_identifier.GetAccountId() ) ).fetchone()
            except: raise HC.ForbiddenException( 'The service could not find that account in its database.' )
            
        
        used_data = ( used_bytes, used_requests )
        
        banned_info = c.execute( 'SELECT reason, created, expires FROM bans, reasons USING ( reason_id ) WHERE service_id = ? AND account_id = ?;', ( service_id, account_id ) ).fetchone()
        
        return HC.Account( account_id, account_type, created, expires, used_data, banned_info = banned_info )
        
    
    def _GetAccountFileInfo( self, c, service_id, account_id ):
        
        ( num_deleted_files, ) = c.execute( 'SELECT COUNT( * ) FROM deleted_files WHERE service_id = ? AND account_id = ?;', ( service_id, account_id ) ).fetchone()
        
        ( num_files, num_files_bytes ) = c.execute( 'SELECT COUNT( * ), SUM( size ) FROM file_map, files_info USING ( hash_id ) WHERE service_id = ? AND account_id = ?;', ( service_id, account_id ) ).fetchone()
        
        if num_files_bytes is None: num_files_bytes = 0
        
        result = c.execute( 'SELECT score FROM account_scores WHERE service_id = ? AND account_id = ? AND score_type = ?;', ( service_id, account_id, HC.SCORE_PETITION ) ).fetchone()
        
        if result is None: petition_score = 0
        else: ( petition_score, ) = result
        
        ( num_petitions, ) = c.execute( 'SELECT COUNT( DISTINCT reason_id ) FROM file_petitions WHERE service_id = ? AND account_id = ?;', ( service_id, account_id ) ).fetchone()
        
        account_info = {}
        
        account_info[ 'num_deleted_files' ] = num_deleted_files
        account_info[ 'num_files' ] = num_files
        account_info[ 'num_files_bytes' ] = num_files_bytes
        account_info[ 'petition_score' ] = petition_score
        account_info[ 'num_petitions' ] = num_petitions
        
        return account_info
        
    
    def _GetAccountIdFromContactKey( self, c, service_id, contact_key ):
        
        try:
            
            ( account_id, ) = c.execute( 'SELECT account_id FROM contacts WHERE service_id = ? AND contact_key = ?;', ( service_id, sqlite3.Binary( contact_key ) ) ).fetchone()
            
        except: raise HC.NotFoundException( 'Could not find that contact key!' )
        
        return account_id
        
    
    def _GetAccountIds( self, c, access_keys ):
        
        account_ids = []
        
        if type( access_keys ) == set: access_keys = list( access_keys )
        
        for i in range( 0, len( access_keys ), 250 ): # there is a limit on the number of parameterised variables in sqlite, so only do a few at a time
            
            access_keys_subset = access_keys[ i : i + 250 ]
            
            account_ids.extend( [ account_id for ( account_id , ) in c.execute( 'SELECT account_id FROM accounts WHERE access_key IN (' + ','.join( '?' * len( access_keys_subset ) ) + ');', [ sqlite3.Binary( hashlib.sha256( access_key ).digest() ) for access_key in access_keys_subset ] ) ] )
            
        
        return account_ids
        
    
    def _GetAccountMappingInfo( self, c, service_id, account_id ):
        
        ( num_deleted_mappings, ) = c.execute( 'SELECT COUNT( * ) FROM deleted_mappings WHERE service_id = ? AND account_id = ?;', ( service_id, account_id ) ).fetchone()
        
        ( num_mappings, ) = c.execute( 'SELECT COUNT( * ) FROM mappings WHERE service_id = ? AND account_id = ?;', ( service_id, account_id ) ).fetchone()
        
        result = c.execute( 'SELECT score FROM account_scores WHERE service_id = ? AND account_id = ? AND score_type = ?;', ( service_id, account_id, HC.SCORE_PETITION ) ).fetchone()
        
        if result is None: petition_score = 0
        else: ( petition_score, ) = result
        
        # crazy query here because two distinct columns
        ( num_petitions, ) = c.execute( 'SELECT COUNT( * ) FROM ( SELECT DISTINCT tag_id, reason_id FROM mapping_petitions WHERE service_id = ? AND account_id = ? );', ( service_id, account_id ) ).fetchone()
        
        account_info = {}
        
        account_info[ 'num_deleted_mappings' ] = num_deleted_mappings
        account_info[ 'num_mappings' ] = num_mappings
        account_info[ 'petition_score' ] = petition_score
        account_info[ 'num_petitions' ] = num_petitions
        
        return account_info
        
    
    def _GetAccountTypeId( self, c, service_id, title ):
        
        result = c.execute( 'SELECT account_type_id FROM account_types, account_type_map USING ( account_type_id ) WHERE service_id = ? AND title = ?;', ( service_id, title ) ).fetchone()
        
        if result is None: raise HC.NotFoundException( 'Could not find account title ' + str( title ) + ' in db for this service.' )
        
        ( account_type_id, ) = result
        
        return account_type_id
        
    
    def _GetAccountTypes( self, c, service_id ):
        
        return [ account_type for ( account_type, ) in c.execute( 'SELECT account_type FROM account_type_map, account_types USING ( account_type_id ) WHERE service_id = ?;', ( service_id, ) ) ]
        
    
    def _GetCachedUpdate( self, c, service_id, begin ):
        
        result = c.execute( 'SELECT update_key FROM update_cache WHERE service_id = ? AND begin = ?;', ( service_id, begin ) ).fetchone()
        
        if result is None: result = c.execute( 'SELECT update_key FROM update_cache WHERE service_id = ? AND ? BETWEEN begin AND end;', ( service_id, begin ) ).fetchone()
        
        if result is None: raise HC.NotFoundException( 'Could not find that update!' )
        
        ( update_key, ) = result
        
        with open( HC.SERVER_UPDATES_DIR + os.path.sep + update_key, 'rb' ) as f: update = f.read()
        
        return update
        
    
    def _GetDirtyUpdates( self, c ): return c.execute( 'SELECT service_id, begin, end FROM update_cache WHERE dirty = ?;', ( True, ) ).fetchall()
    
    def _GetOptions( self, c, service_id ):
        
        ( options, ) = c.execute( 'SELECT options FROM services WHERE service_id = ?;', ( service_id, ) ).fetchone()
        
        return options
        
    
    def _GetReason( self, c, reason_id ):
        
        result = c.execute( 'SELECT reason FROM reasons WHERE reason_id = ?;', ( reason_id, ) ).fetchone()
        
        if result is None: raise Exception( 'Reason error in database' )
        
        ( reason, ) = result
        
        return reason
        
    
    def _GetReasonId( self, c, reason ):
        
        result = c.execute( 'SELECT reason_id FROM reasons WHERE reason = ?;', ( reason, ) ).fetchone()
        
        if result is None:
            
            c.execute( 'INSERT INTO reasons ( reason ) VALUES ( ? );', ( reason, ) )
            
            reason_id = c.lastrowid
            
            return reason_id
            
        else:
            
            ( reason_id, ) = result
            
            return reason_id
            
        
    
    def _GetRestrictedServiceStats( self, c, service_id ):
        
        stats = {}
        
        ( stats[ 'num_accounts' ], ) = c.execute( 'SELECT COUNT( * ) FROM account_map WHERE service_id = ?;', ( service_id, ) ).fetchone()
        
        ( stats[ 'num_banned' ], ) = c.execute( 'SELECT COUNT( * ) FROM bans WHERE service_id = ?;', ( service_id, ) ).fetchone()
        
        return stats
        
    
    def _GetServiceId( self, c, service_identifier ):
        
        service_type = service_identifier.GetType()
        port = service_identifier.GetPort()
        
        result = c.execute( 'SELECT service_id FROM services WHERE type = ? AND port = ?;', ( service_type, port ) ).fetchone()
        
        if result is None: raise Exception( 'Service id error in database' )
        
        ( service_id, ) = result
        
        return service_id
        
    
    def _GetServiceIds( self, c, limited_types = HC.ALL_SERVICES ): return [ service_id for ( service_id, ) in c.execute( 'SELECT service_id FROM services WHERE type IN ' + HC.SplayListForDB( limited_types ) + ';' ) ]
    
    def _GetServiceIdentifiers( self, c, limited_types = HC.ALL_SERVICES ): return [ HC.ServerServiceIdentifier( service_type, port ) for ( service_type, port ) in c.execute( 'SELECT type, port FROM services WHERE type IN '+ HC.SplayListForDB( limited_types ) + ';' ) ]
    
    def _GetServiceType( self, c, service_id ):
        
        result = c.execute( 'SELECT type FROM services WHERE service_id = ?;', ( service_id, ) ).fetchone()
        
        if result is None: raise Exception( 'Service id error in database' )
        
        ( service_type, ) = result
        
        return service_type
        
    
    def _GetUpdateEnds( self, c ):
        
        service_ids = self._GetServiceIds( c, HC.REPOSITORIES )
        
        ends = [ c.execute( 'SELECT service_id, end FROM update_cache WHERE service_id = ? ORDER BY end DESC LIMIT 1;', ( service_id, ) ).fetchone() for service_id in service_ids ]
        
        return ends
        
    
    def _ModifyAccountTypes( self, c, service_id, edit_log ):
        
        for ( action, details ) in edit_log:
            
            if action == 'add':
                
                account_type = details
                
                title = account_type.GetTitle()
                
                if self._AccountTypeExists( c, service_id, title ): raise HC.ForbiddenException( 'Already found account type ' + str( title ) + ' in the db for this service, so could not add!' )
                
                c.execute( 'INSERT OR IGNORE INTO account_types ( title, account_type ) VALUES ( ?, ? );', ( title, account_type ) )
                
                account_type_id = c.lastrowid
                
                c.execute( 'INSERT OR IGNORE INTO account_type_map ( service_id, account_type_id ) VALUES ( ?, ? );', ( service_id, account_type_id ) )
                
            elif action == 'delete':
                
                ( title, new_title ) = details
                
                account_type_id = self._GetAccountId( c, service_id, title )
                
                new_account_type_id = self._GetAccountId( c, service_id, new_title )
                
                c.execute( 'UPDATE account_map SET account_type_id = ? WHERE service_id = ? AND account_type_id = ?;', ( new_account_type_id, service_id, account_type_id ) )
                
                c.execute( 'DELETE FROM account_types WHERE account_type_id = ?;', ( account_type_id, ) )
                
                c.execute( 'DELETE FROM account_type_map WHERE service_id = ? AND account_type_id = ?;', ( service_id, account_type_id ) )
                
            elif action == 'edit':
                
                ( old_title, account_type ) = details
                
                title = account_type.GetTitle()
                
                if old_title != title and self._AccountTypeExists( c, service_id, title ): raise HC.ForbiddenException( 'Already found account type ' + str( title ) + ' in the database, so could not rename ' + str( old_title ) + '!' )
                
                account_type_id = self._GetAccountTypeId( c, service_id, old_title )
                
                c.execute( 'UPDATE account_types SET title = ?, account_type = ? WHERE account_type_id = ?;', ( title, account_type, account_type_id ) )
                
            
        
    
    def _ModifyServices( self, c, account_id, edit_log ):
        
        now = int( time.time() )
        
        for ( action, data ) in edit_log:
            
            if action == HC.ADD:
                
                service_identifier = data
                
                service_type = service_identifier.GetType()
                port = service_identifier.GetPort()
                
                if c.execute( 'SELECT 1 FROM services WHERE port = ?;', ( port, ) ).fetchone() is not None: raise Exception( 'There is already a service hosted at port ' + str( port ) )
                
                c.execute( 'INSERT INTO services ( type, port, options ) VALUES ( ?, ?, ? );', ( service_type, port, yaml.safe_dump( HC.DEFAULT_OPTIONS[ service_type ] ) ) )
                
                service_id = c.lastrowid
                
                service_admin_account_type = HC.AccountType( 'service admin', [ HC.GET_DATA, HC.POST_DATA, HC.POST_PETITIONS, HC.RESOLVE_PETITIONS, HC.MANAGE_USERS, HC.GENERAL_ADMIN ], ( None, None ) )
                
                c.execute( 'INSERT INTO account_types ( title, account_type ) VALUES ( ?, ? );', ( 'service admin', service_admin_account_type ) )
                
                service_admin_account_type_id = c.lastrowid
                
                c.execute( 'INSERT INTO account_type_map ( service_id, account_type_id ) VALUES ( ?, ? );', ( service_id, service_admin_account_type_id ) )
                
                c.execute( 'INSERT INTO account_map ( service_id, account_id, account_type_id, created, expires, used_bytes, used_requests ) VALUES ( ?, ?, ?, ?, ?, ?, ? );', ( service_id, account_id, service_admin_account_type_id, now, None, 0, 0 ) )
                
                if service_type in HC.REPOSITORIES:
                    
                    begin = 0
                    end = int( time.time() )
                    
                    if service_type == HC.FILE_REPOSITORY: update = self._GenerateFileUpdate( c, service_id, begin, end )
                    elif service_type == HC.TAG_REPOSITORY: update = self._GenerateMappingUpdate( c, service_id, begin, end )
                    elif service_type in ( HC.RATING_LIKE_REPOSITORY, HC.RATING_NUMERICAL_REPOSITORY ): update = self._GenerateRatingUpdate( c, service_id, begin, end )
                    
                    update_key = os.urandom( 32 )
                    
                    with open( HC.SERVER_UPDATES_DIR + os.path.sep + update_key, 'wb' ) as f: f.write( yaml.safe_dump( update )  )
                    
                    c.execute( 'INSERT INTO update_cache ( service_id, begin, end, update_key ) VALUES ( ?, ?, ?, ? );', ( service_id, begin, end, update_key ) )
                    
                
            elif action == HC.EDIT:
                
                ( service_identifier, new_port ) = data
                
                service_id = self._GetServiceId( c, service_identifier )
                
                if c.execute( 'SELECT 1 FROM services WHERE port = ?;', ( new_port, ) ).fetchone() is not None: raise Exception( 'There is already a service hosted at port ' + str( port ) )
                
                c.execute( 'UPDATE services SET port = ? WHERE service_id = ?;', ( new_port, service_id ) )
                
            elif action == HC.DELETE:
                
                service_identifier = data
                
                service_id = self._GetServiceId( c, service_identifier )
                
                c.execute( 'DELETE FROM services WHERE service_id = ?;', ( service_id, ) )
                
            
        
        # now we are sure the db is happy ( and a commit is like 5ms away), let's boot these servers
        
        desired_service_identifiers = self._GetServiceIdentifiers( c )
        
        existing_servers = dict( self._servers )
        
        for existing_service_identifier in existing_servers.keys():
            
            if existing_service_identifier not in desired_service_identifiers:
                
                self._servers[ existing_service_identifier ].shutdown()
                
                del self._servers[ existing_service_identifier ]
                
            
        
        for desired_service_identifier in desired_service_identifiers:
            
            if desired_service_identifier not in existing_servers:
                
                service_id = self._GetServiceId( c, desired_service_identifier )
                
                options = self._GetOptions( c, service_id )
                
                if 'message' in options: message = options[ 'message' ]
                else: message = ''
                
                server = HydrusServer.HydrusHTTPServer( desired_service_identifier, message )
                
                self._servers[ desired_service_identifier ] = server
                
                threading.Thread( target=server.serve_forever ).start()
                
            
        
    
    def _RefreshUpdateCache( self, c, service_id, affected_timestamps ): c.executemany( 'UPDATE update_cache SET dirty = ? WHERE service_id = ? AND ? BETWEEN begin AND end;', [ ( True, service_id, timestamp ) for timestamp in affected_timestamps ] )
    
    def _RewardAccounts( self, c, service_id, score_type, scores ):
        
        c.executemany( 'INSERT OR IGNORE INTO account_scores ( service_id, account_id, score_type, score ) VALUES ( ?, ?, ?, ? );', [ ( service_id, account_id, score_type, 0 ) for ( account_id, score ) in scores ] )
        
        c.executemany( 'UPDATE account_scores SET score = score + ? WHERE service_id = ? AND account_id = ? and score_type = ?;', [ ( score, service_id, account_id, score_type ) for ( account_id, score ) in scores ] )
        
    
    def _SetExpires( self, c, service_id, account_ids, expires ): c.execute( 'UPDATE account_map SET expires = ? WHERE service_id = ? AND account_id IN ' + HC.SplayListForDB( account_ids ) + ';', ( expires, service_id ) )
    
    def _SetOptions( self, c, service_id, service_identifier, options ):
        
        c.execute( 'UPDATE services SET options = ? WHERE service_id = ?;', ( options, service_id ) ).fetchone()
        
        if 'message' in options:
            
            message = options[ 'message' ]
            
            self._servers[ service_identifier ].SetMessage( message )
            
        
    
    def _UnbanKey( self, c, service_id, account_id ): c.execute( 'DELETE FROM bans WHERE service_id = ? AND account_id = ?;', ( account_id, ) )
    
class DB( ServiceDB ):
    
    def __init__( self ):
        
        self._db_path = HC.DB_DIR + os.path.sep + 'server.db'
        
        self._jobs = Queue.PriorityQueue()
        self._pubsubs = []
        
        self._over_monthly_data = False
        self._services_over_monthly_data = set()
        
        self._InitDB()
        
        ( db, c ) = self._GetDBCursor()
        
        self._UpdateDB( c )
        
        service_identifiers = self._GetServiceIdentifiers( c )
        
        ( self._server_admin_id, ) = c.execute( 'SELECT service_id FROM services WHERE type = ?;', ( HC.SERVER_ADMIN, ) ).fetchone()
        
        self._servers = {}
        
        for service_identifier in service_identifiers:
            
            service_type = service_identifier.GetType()
            
            if service_type == HC.SERVER_ADMIN:
                
                port = service_identifier.GetPort()
                
                try:
                    
                    connection = httplib.HTTPConnection( '127.0.0.1', port )
                    
                    connection.connect()
                    
                    connection.close()
                    
                    already_running = True
                    
                except:
                    
                    already_running = False
                    
                
                if already_running: raise Exception( 'The server appears to be running already!' + os.linesep + 'Either that, or something else is using port ' + str( port ) + '.' )
                
            
            service_id = self._GetServiceId( c, service_identifier )
            
            options = self._GetOptions( c, service_id )
            
            if 'message' in options: message = options[ 'message' ]
            else: message = ''
            
            self._servers[ service_identifier ] = HydrusServer.HydrusHTTPServer( service_identifier, message )
            
        
        for server in self._servers.values(): threading.Thread( target=server.serve_forever ).start()
        
        HC.DAEMONQueue( 'FlushRequestsMade', self.DAEMONFlushRequestsMade, 'request_made', period = 10 )
        
        HC.DAEMONWorker( 'CheckMonthlyData', self.DAEMONCheckMonthlyData, period = 3600 )
        HC.DAEMONWorker( 'ClearBans', self.DAEMONClearBans, period = 3600 )
        HC.DAEMONWorker( 'DeleteOrphans', self.DAEMONDeleteOrphans, period = 86400 )
        HC.DAEMONWorker( 'GenerateUpdates', self.DAEMONGenerateUpdates, period = 1200 )
        HC.DAEMONWorker( 'CheckDataUsage', self.DAEMONCheckDataUsage, period = 86400 )
        
        threading.Thread( target = self.MainLoop, name = 'Database Main Loop' ).start()
        
    
    def _GetDBCursor( self ):
        
        db = sqlite3.connect( self._db_path, timeout=600.0, isolation_level = None, detect_types = sqlite3.PARSE_DECLTYPES )
        
        c = db.cursor()
        
        c.execute( 'PRAGMA cache_size = 10000;' )
        c.execute( 'PRAGMA foreign_keys = ON;' )
        c.execute( 'PRAGMA recursive_triggers = ON;' )
        
        return ( db, c )
        
    
    def _InitDB( self ):
        
        if not os.path.exists( self._db_path ):
            
            if not os.path.exists( HC.SERVER_FILES_DIR ): os.mkdir( HC.SERVER_FILES_DIR )
            if not os.path.exists( HC.SERVER_THUMBNAILS_DIR ): os.mkdir( HC.SERVER_THUMBNAILS_DIR )
            if not os.path.exists( HC.SERVER_MESSAGES_DIR ): os.mkdir( HC.SERVER_MESSAGES_DIR )
            if not os.path.exists( HC.SERVER_UPDATES_DIR ): os.mkdir( HC.SERVER_UPDATES_DIR )
            
            ( db, c ) = self._GetDBCursor()
            
            c.execute( 'BEGIN IMMEDIATE' )
            
            c.execute( 'PRAGMA auto_vacuum = 0;' ) # none
            c.execute( 'PRAGMA journal_mode=WAL;' )
            
            now = int( time.time() )
            
            c.execute( 'CREATE TABLE services ( service_id INTEGER PRIMARY KEY, type INTEGER, port INTEGER, options TEXT_YAML );' )
            
            c.execute( 'CREATE TABLE accounts ( account_id INTEGER PRIMARY KEY, access_key BLOB_BYTES );' )
            c.execute( 'CREATE UNIQUE INDEX accounts_access_key_index ON accounts ( access_key );' )
            
            c.execute( 'CREATE TABLE account_map ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, account_type_id INTEGER, created INTEGER, expires INTEGER, used_bytes INTEGER, used_requests INTEGER, PRIMARY KEY( service_id, account_id ) );' )
            c.execute( 'CREATE INDEX account_map_service_id_account_type_id_index ON account_map ( service_id, account_type_id );' )
            
            c.execute( 'CREATE TABLE account_scores ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, score_type INTEGER, score INTEGER, PRIMARY KEY( service_id, account_id, score_type ) );' )
            
            c.execute( 'CREATE TABLE account_type_map ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_type_id, PRIMARY KEY ( service_id, account_type_id ) );' )
            
            c.execute( 'CREATE TABLE account_types ( account_type_id INTEGER PRIMARY KEY, title TEXT, account_type TEXT_YAML );' )
            
            c.execute( 'CREATE TABLE bans ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, admin_account_id INTEGER, reason_id INTEGER, created INTEGER, expires INTEGER, PRIMARY KEY( service_id, account_id ) );' )
            c.execute( 'CREATE INDEX bans_expires ON bans ( expires );' )
            
            c.execute( 'CREATE TABLE contacts ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, contact_key BLOB, public_key TEXT, PRIMARY KEY( service_id, account_id ) );' )
            c.execute( 'CREATE UNIQUE INDEX contacts_contact_key_index ON contacts ( contact_key );' )
            
            c.execute( 'CREATE TABLE deleted_files ( service_id INTEGER REFERENCES services ON DELETE CASCADE, hash_id INTEGER, reason_id INTEGER, account_id INTEGER, admin_account_id INTEGER, timestamp INTEGER, PRIMARY KEY( service_id, hash_id ) );' )
            c.execute( 'CREATE INDEX deleted_files_service_id_account_id_index ON deleted_files ( service_id, account_id );' )
            c.execute( 'CREATE INDEX deleted_files_service_id_timestamp_index ON deleted_files ( service_id, timestamp );' )
            
            c.execute( 'CREATE TABLE deleted_mappings ( service_id INTEGER REFERENCES services ON DELETE CASCADE, tag_id INTEGER, hash_id INTEGER, reason_id INTEGER, account_id INTEGER, admin_account_id INTEGER, timestamp INTEGER, PRIMARY KEY( service_id, tag_id, hash_id ) );' )
            c.execute( 'CREATE INDEX deleted_mappings_service_id_account_id_index ON deleted_mappings ( service_id, account_id );' )
            c.execute( 'CREATE INDEX deleted_mappings_service_id_timestamp_index ON deleted_mappings ( service_id, timestamp );' )
            
            c.execute( 'CREATE TABLE files_info ( hash_id INTEGER PRIMARY KEY, size INTEGER, mime INTEGER, width INTEGER, height INTEGER, duration INTEGER, num_frames INTEGER, num_words INTEGER );' )
            
            c.execute( 'CREATE TABLE file_map ( service_id INTEGER REFERENCES services ON DELETE CASCADE, hash_id INTEGER, account_id INTEGER, timestamp INTEGER, PRIMARY KEY( service_id, hash_id, account_id ) );' )
            c.execute( 'CREATE INDEX file_map_service_id_account_id_index ON file_map ( service_id, account_id );' )
            c.execute( 'CREATE INDEX file_map_service_id_timestamp_index ON file_map ( service_id, timestamp );' )
            
            c.execute( 'CREATE TABLE file_petitions ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, hash_id INTEGER, reason_id INTEGER, PRIMARY KEY( service_id, account_id, hash_id ) );' )
            c.execute( 'CREATE INDEX file_petitions_service_id_account_id_reason_id_index ON file_petitions ( service_id, account_id, reason_id );' )
            c.execute( 'CREATE INDEX file_petitions_service_id_hash_id_index ON file_petitions ( service_id, hash_id );' )
            
            c.execute( 'CREATE TABLE hashes ( hash_id INTEGER PRIMARY KEY, hash BLOB_BYTES );' )
            c.execute( 'CREATE UNIQUE INDEX hashes_hash_index ON hashes ( hash );' )
            
            c.execute( 'CREATE TABLE ip_addresses ( service_id INTEGER REFERENCES services ON DELETE CASCADE, hash_id INTEGER, ip TEXT, timestamp INTEGER, PRIMARY KEY( service_id, hash_id ) );' )
            
            c.execute( 'CREATE TABLE mapping_petitions ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, tag_id INTEGER, hash_id INTEGER, reason_id INTEGER, PRIMARY KEY( service_id, account_id, tag_id, hash_id ) );' )
            c.execute( 'CREATE INDEX mapping_petitions_service_id_account_id_reason_id_tag_id_index ON mapping_petitions ( service_id, account_id, reason_id, tag_id );' )
            c.execute( 'CREATE INDEX mapping_petitions_service_id_tag_id_hash_id_index ON mapping_petitions ( service_id, tag_id, hash_id );' )
            
            c.execute( 'CREATE TABLE mappings ( service_id INTEGER REFERENCES services ON DELETE CASCADE, tag_id INTEGER, hash_id INTEGER, account_id INTEGER, timestamp INTEGER, PRIMARY KEY( service_id, tag_id, hash_id ) );' )
            
            c.execute( 'CREATE TABLE messages ( message_key BLOB_BYTES PRIMARY KEY, service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, timestamp INTEGER );' )
            c.execute( 'CREATE INDEX messages_service_id_account_id_index ON messages ( service_id, account_id );' )
            c.execute( 'CREATE INDEX messages_timestamp_index ON messages ( timestamp );' )
            
            c.execute( 'CREATE TABLE message_statuses ( status_key BLOB_BYTES PRIMARY KEY, service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, status BLOB_BYTES, timestamp INTEGER );' )
            c.execute( 'CREATE INDEX message_statuses_service_id_account_id_index ON message_statuses ( service_id, account_id );' )
            c.execute( 'CREATE INDEX message_statuses_timestamp_index ON message_statuses ( timestamp );' )
            
            c.execute( 'CREATE TABLE news ( service_id INTEGER REFERENCES services ON DELETE CASCADE, news TEXT, timestamp INTEGER );' )
            c.execute( 'CREATE INDEX news_timestamp_index ON news ( timestamp );' )
            
            c.execute( 'CREATE TABLE reasons ( reason_id INTEGER PRIMARY KEY, reason TEXT );' )
            c.execute( 'CREATE UNIQUE INDEX reasons_reason_index ON reasons ( reason );' )
            
            c.execute( 'CREATE TABLE tags ( tag_id INTEGER PRIMARY KEY, tag TEXT );' )
            c.execute( 'CREATE UNIQUE INDEX tags_tag_index ON tags ( tag );' )
            
            c.execute( 'CREATE TABLE update_cache ( service_id INTEGER REFERENCES services ON DELETE CASCADE, begin INTEGER, end INTEGER, update_key TEXT, dirty INTEGER_BOOLEAN, PRIMARY KEY( service_id, begin ) );' )
            c.execute( 'CREATE UNIQUE INDEX update_cache_service_id_end_index ON update_cache ( service_id, end );' )
            c.execute( 'CREATE INDEX update_cache_service_id_dirty_index ON update_cache ( service_id, dirty );' )
            
            c.execute( 'CREATE TABLE version ( version INTEGER, year INTEGER, month INTEGER );' )
            
            current_time_struct = time.gmtime()
            
            ( current_year, current_month ) = ( current_time_struct.tm_year, current_time_struct.tm_mon )
            
            c.execute( 'INSERT INTO version ( version, year, month ) VALUES ( ?, ?, ? );', ( HC.SOFTWARE_VERSION, current_year, current_month ) )
            
            # set up server admin
            
            c.execute( 'INSERT INTO services ( type, port, options ) VALUES ( ?, ?, ? );', ( HC.SERVER_ADMIN, HC.DEFAULT_SERVER_ADMIN_PORT, yaml.safe_dump( HC.DEFAULT_OPTIONS[ HC.SERVER_ADMIN ] ) ) )
            
            server_admin_service_id = c.lastrowid
            
            server_admin_account_type = HC.AccountType( 'server admin', [ HC.MANAGE_USERS, HC.GENERAL_ADMIN, HC.EDIT_SERVICES ], ( None, None ) )
            
            c.execute( 'INSERT INTO account_types ( title, account_type ) VALUES ( ?, ? );', ( 'server admin', server_admin_account_type ) )
            
            server_admin_account_type_id = c.lastrowid
            
            c.execute( 'INSERT INTO account_type_map ( service_id, account_type_id ) VALUES ( ?, ? );', ( server_admin_service_id, server_admin_account_type_id ) )
            
            c.execute( 'COMMIT' )
            
        
    
    def _MakeBackup( self, c ):
        
        c.execute( 'COMMIT' )
        
        c.execute( 'VACUUM' )
        
        c.execute( 'BEGIN IMMEDIATE' )
        
        shutil.copy( self._db_path, self._db_path + '.backup' )
        if os.path.exists( self._db_path + '-wal' ): shutil.copy( self._db_path + '-wal', self._db_path + '-wal.backup' )
        
        shutil.rmtree( HC.SERVER_FILES_DIR + '_backup', ignore_errors = True )
        shutil.rmtree( HC.SERVER_THUMBNAILS_DIR + '_backup', ignore_errors = True )
        shutil.rmtree( HC.SERVER_MESSAGES_DIR + '_backup', ignore_errors = True )
        shutil.rmtree( HC.SERVER_UPDATES_DIR + '_backup', ignore_errors = True )
        
        shutil.copytree( HC.SERVER_FILES_DIR, HC.SERVER_FILES_DIR + '_backup' )
        shutil.copytree( HC.SERVER_THUMBNAILS_DIR, HC.SERVER_THUMBNAILS_DIR + '_backup' )
        shutil.copytree( HC.SERVER_MESSAGES_DIR, HC.SERVER_MESSAGES_DIR + '_backup' )
        shutil.copytree( HC.SERVER_UPDATES_DIR, HC.SERVER_UPDATES_DIR + '_backup' )
        
    
    def _UpdateDB( self, c ):
        
        ( version, ) = c.execute( 'SELECT version FROM version;' ).fetchone()
        
        if version != HC.SOFTWARE_VERSION:
            
            c.execute( 'BEGIN IMMEDIATE' )
            
            try:
                
                self._UpdateDBOld( c, version )
                
                if version < 37:
                    
                    os.mkdir( HC.SERVER_MESSAGES_DIR )
                    
                    c.execute( 'CREATE TABLE contacts ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, contact_key BLOB, public_key TEXT, PRIMARY KEY( service_id, account_id ) );' )
                    c.execute( 'CREATE UNIQUE INDEX contacts_contact_key_index ON contacts ( contact_key );' )
                    
                    c.execute( 'CREATE TABLE messages ( message_key BLOB PRIMARY KEY, service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, timestamp INTEGER );' )
                    c.execute( 'CREATE INDEX messages_service_id_account_id_index ON messages ( service_id, account_id );' )
                    c.execute( 'CREATE INDEX messages_timestamp_index ON messages ( timestamp );' )
                    
                
                if version < 38:
                    
                    c.execute( 'COMMIT' )
                    c.execute( 'PRAGMA journal_mode=WAL;' ) # possibly didn't work last time, cause of sqlite dll issue
                    c.execute( 'BEGIN IMMEDIATE' )
                    
                    c.execute( 'DROP TABLE messages;' ) # blob instead of blob_bytes!
                    
                    c.execute( 'CREATE TABLE messages ( message_key BLOB_BYTES PRIMARY KEY, service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, timestamp INTEGER );' )
                    c.execute( 'CREATE INDEX messages_service_id_account_id_index ON messages ( service_id, account_id );' )
                    c.execute( 'CREATE INDEX messages_timestamp_index ON messages ( timestamp );' )
                    
                    c.execute( 'CREATE TABLE message_statuses ( status_key BLOB_BYTES PRIMARY KEY, service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, status BLOB_BYTES, timestamp INTEGER );' )
                    c.execute( 'CREATE INDEX message_statuses_service_id_account_id_index ON message_statuses ( service_id, account_id );' )
                    c.execute( 'CREATE INDEX message_statuses_timestamp_index ON message_statuses ( timestamp );' )
                    
                
                if version < 40:
                    
                    try: c.execute( 'SELECT 1 FROM message_statuses;' ).fetchone() # didn't update dbinit on 38
                    except:
                        
                        c.execute( 'DROP TABLE messages;' ) # blob instead of blob_bytes!
                        
                        c.execute( 'CREATE TABLE messages ( message_key BLOB_BYTES PRIMARY KEY, service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, timestamp INTEGER );' )
                        c.execute( 'CREATE INDEX messages_service_id_account_id_index ON messages ( service_id, account_id );' )
                        c.execute( 'CREATE INDEX messages_timestamp_index ON messages ( timestamp );' )
                        
                        c.execute( 'CREATE TABLE message_statuses ( status_key BLOB_BYTES PRIMARY KEY, service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, status BLOB_BYTES, timestamp INTEGER );' )
                        c.execute( 'CREATE INDEX message_statuses_service_id_account_id_index ON message_statuses ( service_id, account_id );' )
                        c.execute( 'CREATE INDEX message_statuses_timestamp_index ON message_statuses ( timestamp );' )
                        
                    
                
                if version < 50:
                    
                    c.execute( 'CREATE TABLE ratings ( service_id INTEGER REFERENCES services ON DELETE CASCADE, account_id INTEGER, hash_id INTEGER, rating REAL, PRIMARY KEY( service_id, account_id, hash_id ) );' )
                    c.execute( 'CREATE INDEX ratings_hash_id ON ratings ( hash_id );' )
                    
                    c.execute( 'CREATE TABLE ratings_aggregates ( service_id INTEGER REFERENCES services ON DELETE CASCADE, hash_id INTEGER, score INTEGER, count INTEGER, new_timestamp INTEGER, current_timestamp INTEGER, PRIMARY KEY( service_id, hash_id ) );' )
                    c.execute( 'CREATE INDEX ratings_aggregates_new_timestamp ON ratings_aggregates ( new_timestamp );' )
                    c.execute( 'CREATE INDEX ratings_aggregates_current_timestamp ON ratings_aggregates ( current_timestamp );' )
                    
                
                if version < 51:
                    
                    if not os.path.exists( HC.SERVER_UPDATES_DIR ): os.mkdir( HC.SERVER_UPDATES_DIR )
                    
                    all_update_data = c.execute( 'SELECT * FROM update_cache;' ).fetchall()
                    
                    c.execute( 'DROP TABLE update_cache;' )
                    
                    c.execute( 'CREATE TABLE update_cache ( service_id INTEGER REFERENCES services ON DELETE CASCADE, begin INTEGER, end INTEGER, update_key TEXT, dirty INTEGER_BOOLEAN, PRIMARY KEY( service_id, begin ) );' )
                    c.execute( 'CREATE UNIQUE INDEX update_cache_service_id_end_index ON update_cache ( service_id, end );' )
                    c.execute( 'CREATE INDEX update_cache_service_id_dirty_index ON update_cache ( service_id, dirty );' )
                    
                    for ( service_id, begin, end, update, dirty ) in all_update_data:
                        
                        update_key_bytes = os.urandom( 32 )
                        
                        update_key = update_key_bytes.encode( 'hex' )
                        
                        with open( HC.SERVER_UPDATES_DIR + os.path.sep + update_key, 'wb' ) as f: f.write( update )
                        
                        c.execute( 'INSERT INTO update_cache ( service_id, begin, end, update_key, dirty ) VALUES ( ?, ?, ?, ?, ? );', ( service_id, begin, end, update_key, dirty ) )
                        
                    
                
                if version < 56:
                    
                    c.execute( 'DROP INDEX mappings_service_id_account_id_index;' )
                    c.execute( 'DROP INDEX mappings_service_id_timestamp_index;' )
                    
                    c.execute( 'CREATE INDEX mappings_account_id_index ON mappings ( account_id );' )
                    c.execute( 'CREATE INDEX mappings_timestamp_index ON mappings ( timestamp );' )
                    
                
                c.execute( 'UPDATE version SET version = ?;', ( HC.SOFTWARE_VERSION, ) )
                
                c.execute( 'COMMIT' )
                
                wx.MessageBox( 'The server has updated successfully!' )
                
            except:
                
                c.execute( 'ROLLBACK' )
                
                print( traceback.format_exc() )
                
                raise Exception( 'Tried to update the server db, but something went wrong:' + os.linesep + traceback.format_exc() )
                
            
        
        self._UpdateDBOldPost( c, version )
        
    
    def _UpdateDBOld( self, c, version ):
        
        if version < 29:
            
            files_db_path = HC.DB_DIR + os.path.sep + 'server_files.db'
            
            c.execute( 'COMMIT' )
            
            # can't do it inside a transaction
            c.execute( 'ATTACH database "' + files_db_path + '" as files_db;' )
            
            c.execute( 'BEGIN IMMEDIATE' )
            
            os.mkdir( HC.SERVER_FILES_DIR )
            
            all_local_files = [ hash_id for ( hash_id, ) in c.execute( 'SELECT hash_id FROM files;' ) ]
            
            for i in range( 0, len( all_local_files ), 100 ):
                
                local_files_subset = all_local_files[ i : i + 100 ]
                
                for hash_id in local_files_subset:
                    
                    hash = self._GetHash( c, hash_id )
                    
                    ( file, ) = c.execute( 'SELECT file FROM files WHERE hash_id = ?', ( hash_id, ) ).fetchone()
                    
                    path_to = HC.SERVER_FILES_DIR + os.path.sep + hash.encode( 'hex' )
                    
                    with open( path_to, 'wb' ) as f: f.write( file )
                    
                
                c.execute( 'DELETE FROM files WHERE hash_id IN ' + HC.SplayListForDB( local_files_subset ) + ';' )
                
                c.execute( 'COMMIT' )
                
                # slow truncate happens here!
                
                c.execute( 'BEGIN IMMEDIATE' )
                
            
            c.execute( 'COMMIT' )
            
            # can't do it inside a transaction
            c.execute( 'DETACH DATABASE files_db;' )
            
            c.execute( 'BEGIN IMMEDIATE' )
            
            os.remove( files_db_path )
            
        
        if version < 30:
            
            thumbnails_db_path = HC.DB_DIR + os.path.sep + 'server_thumbnails.db'
            
            c.execute( 'COMMIT' )
            
            # can't do it inside a transaction
            c.execute( 'ATTACH database "' + thumbnails_db_path + '" as thumbnails_db;' )
            
            os.mkdir( HC.SERVER_THUMBNAILS_DIR )
            
            all_thumbnails = c.execute( 'SELECT DISTINCT hash_id, hash FROM thumbnails, hashes USING ( hash_id );' ).fetchall()
            
            for i in range( 0, len( all_thumbnails ), 500 ):
                
                thumbnails_subset = all_thumbnails[ i : i + 500 ]
                
                for ( hash_id, hash ) in thumbnails_subset:
                    
                    ( thumbnail, ) = c.execute( 'SELECT thumbnail FROM thumbnails WHERE hash_id = ?', ( hash_id, ) ).fetchone()
                    
                    path_to = HC.SERVER_THUMBNAILS_DIR + os.path.sep + hash.encode( 'hex' )
                    
                    with open( path_to, 'wb' ) as f: f.write( thumbnail )
                    
                
            
            # can't do it inside a transaction
            c.execute( 'DETACH DATABASE thumbnails_db;' )
            
            c.execute( 'BEGIN IMMEDIATE' )
            
            os.remove( thumbnails_db_path )
            
        
    
    def _UpdateDBOldPost( self, c, version ):
        
        if version == 34: # == is important here
            
            try:
                
                main_db_path = HC.DB_DIR + os.path.sep + 'server_main.db'
                files_info_db_path = HC.DB_DIR + os.path.sep + 'server_files_info.db'
                mappings_db_path = HC.DB_DIR + os.path.sep + 'server_mappings.db'
                updates_db_path = HC.DB_DIR + os.path.sep + 'server_updates.db'
                
                if os.path.exists( main_db_path ):
                    
                    # can't do it inside transaction
                    
                    c.execute( 'ATTACH database "' + main_db_path + '" as main_db;' )
                    c.execute( 'ATTACH database "' + files_info_db_path + '" as files_info_db;' )
                    c.execute( 'ATTACH database "' + mappings_db_path + '" as mappings_db;' )
                    c.execute( 'ATTACH database "' + updates_db_path + '" as updates_db;' )
                    
                    c.execute( 'BEGIN IMMEDIATE' )
                    
                    c.execute( 'REPLACE INTO main.services SELECT * FROM main_db.services;' )
                    
                    all_service_ids = [ service_id for ( service_id, ) in c.execute( 'SELECT service_id FROM main.services;' ) ]
                    
                    c.execute( 'DELETE FROM main_db.account_map WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM main_db.account_scores WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM main_db.account_type_map WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM main_db.bans WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM main_db.news WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    
                    c.execute( 'DELETE FROM mappings_db.deleted_mappings WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM mappings_db.mappings WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM mappings_db.mapping_petitions WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    
                    c.execute( 'DELETE FROM files_info_db.deleted_files WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM files_info_db.file_map WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM files_info_db.ip_addresses WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    c.execute( 'DELETE FROM files_info_db.file_petitions WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    
                    c.execute( 'DELETE FROM updates_db.update_cache WHERE service_id NOT IN ' + HC.SplayListForDB( all_service_ids ) + ';' )
                    
                    c.execute( 'REPLACE INTO main.accounts SELECT * FROM main_db.accounts;' )
                    c.execute( 'REPLACE INTO main.account_map SELECT * FROM main_db.account_map;' )
                    c.execute( 'REPLACE INTO main.account_scores SELECT * FROM main_db.account_scores;' )
                    c.execute( 'REPLACE INTO main.account_type_map SELECT * FROM main_db.account_type_map;' )
                    c.execute( 'REPLACE INTO main.account_types SELECT * FROM main_db.account_types;' )
                    c.execute( 'REPLACE INTO main.bans SELECT * FROM main_db.bans;' )
                    c.execute( 'REPLACE INTO main.hashes SELECT * FROM main_db.hashes;' )
                    c.execute( 'REPLACE INTO main.news SELECT * FROM main_db.news;' )
                    c.execute( 'REPLACE INTO main.reasons SELECT * FROM main_db.reasons;' )
                    c.execute( 'REPLACE INTO main.tags SELECT * FROM main_db.tags;' )
                    # don't do version, lol
                    
                    c.execute( 'REPLACE INTO main.deleted_mappings SELECT * FROM mappings_db.deleted_mappings;' )
                    c.execute( 'REPLACE INTO main.mappings SELECT * FROM mappings_db.mappings;' )
                    c.execute( 'REPLACE INTO main.mapping_petitions SELECT * FROM mappings_db.mapping_petitions;' )
                    
                    c.execute( 'REPLACE INTO main.deleted_files SELECT * FROM files_info_db.deleted_files;' )
                    c.execute( 'REPLACE INTO main.files_info SELECT * FROM files_info_db.files_info;' )
                    c.execute( 'REPLACE INTO main.file_map SELECT * FROM files_info_db.file_map;' )
                    c.execute( 'REPLACE INTO main.file_petitions SELECT * FROM files_info_db.file_petitions;' )
                    c.execute( 'REPLACE INTO main.ip_addresses SELECT * FROM files_info_db.ip_addresses;' )
                    
                    c.execute( 'REPLACE INTO main.update_cache SELECT * FROM updates_db.update_cache;' )
                    
                    c.execute( 'COMMIT' )
                    
                    c.execute( 'DETACH database main_db;' )
                    c.execute( 'DETACH database files_info_db;' )
                    c.execute( 'DETACH database mappings_db;' )
                    c.execute( 'DETACH database updates_db;' )
                    
                    os.remove( main_db_path )
                    os.remove( mappings_db_path )
                    os.remove( files_info_db_path )
                    os.remove( updates_db_path )
                    
                
            except:
                
                print( traceback.format_exc() )
                
                try: c.execute( 'ROLLBACK' )
                except: pass
                
                raise Exception( 'Tried to update the server db, but something went wrong:' + os.linesep + traceback.format_exc() )
                
            
        
    
    def AddJobServer( self, service_identifier, account_identifier, ip, request_type, request, request_args, request_length ):
        
        priority = HC.HIGH_PRIORITY
        
        job = HC.JobServer( service_identifier, account_identifier, ip, request_type, request, request_args, request_length )
        
        self._jobs.put( ( priority, job ) )
        
        if not HC.shutdown: return job.GetResult()
        
        raise Exception( 'Application quit before db could serve result!' )
        
    
    def DAEMONCheckDataUsage( self ): self.Write( 'check_data_usage', HC.LOW_PRIORITY )
    
    def DAEMONCheckMonthlyData( self ): self.Write( 'check_monthly_data', HC.LOW_PRIORITY )
    
    def DAEMONClearBans( self ): self.Write( 'clear_bans', HC.LOW_PRIORITY )
    
    def DAEMONDeleteOrphans( self ): self.Write( 'delete_orphans', HC.LOW_PRIORITY )
    
    def DAEMONFlushRequestsMade( self, all_services_requests ): self.Write( 'flush_requests_made', HC.LOW_PRIORITY, all_services_requests )
    
    def DAEMONGenerateUpdates( self ):
        
        dirty_updates = self.Read( 'dirty_updates', HC.LOW_PRIORITY )
        
        for ( service_id, begin, end ) in dirty_updates: self.Write( 'clean_update', HC.LOW_PRIORITY, service_id, begin, end )
        
        update_ends = self.Read( 'update_ends', HC.LOW_PRIORITY )
        
        for ( service_id, biggest_end ) in update_ends:
            
            now = int( time.time() )
            
            next_begin = biggest_end + 1
            next_end = biggest_end + HC.UPDATE_DURATION
            
            while next_end < now:
                
                self.Write( 'create_update', HC.LOW_PRIORITY, service_id, next_begin, next_end )
                
                biggest_end = next_end
                
                now = int( time.time() )
                
                next_begin = biggest_end + 1
                next_end = biggest_end + HC.UPDATE_DURATION
                
            
        
    
    def _MainLoop_JobInternal( self, c, job ):
        
        job_type = job.GetType()
        
        if job_type in ( 'read', 'read_write' ):
            
            if job_type == 'read': c.execute( 'BEGIN DEFERRED' )
            else: c.execute( 'BEGIN IMMEDIATE' )
            
            try:
                
                action = job.GetAction()
                
                result = self._MainLoop_Read( c, action )
                
                c.execute( 'COMMIT' )
                
                for ( topic, args, kwargs ) in self._pubsubs: HC.pubsub.pub( topic, *args, **kwargs )
                
                job.PutResult( result )
                
            except Exception as e:
                
                c.execute( 'ROLLBACK' )
                
                print( 'while attempting a read on the database, the hydrus client encountered the following problem:' )
                print( traceback.format_exc() )
                
                ( exception_type, value, tb ) = sys.exc_info()
                
                new_e = type( e )( os.linesep.join( traceback.format_exception( exception_type, value, tb ) ) )
                
                job.PutResult( new_e )
                
            
        else:
            
            if job_type == 'write': c.execute( 'BEGIN IMMEDIATE' )
            
            try:
                
                action = job.GetAction()
                
                args = job.GetArgs()
                
                self._MainLoop_Write( c, action, args )
                
                if job_type == 'write': c.execute( 'COMMIT' )
                
                for ( topic, args, kwargs ) in self._pubsubs: HC.pubsub.pub( topic, *args, **kwargs )
                
            except Exception as e:
                
                if job_type == 'write': c.execute( 'ROLLBACK' )
                
                print( 'while attempting a write on the database, the hydrus client encountered the following problem:' )
                print( traceback.format_exc() )
                
            
        
    
    def _MainLoop_JobServer( self, c, job ):
        
        ( service_identifier, account_identifier, ip, request_type, request, request_args, request_length ) = job.GetInfo()
        
        service_type = service_identifier.GetType()
        
        if ( service_type, request_type, request ) in HC.BANDWIDTH_CONSUMING_REQUESTS and ( self._over_monthly_data or service_identifier in self._services_over_monthly_data ):  job.PutResult( HC.PermissionException( 'This service has exceeded its monthly data allowance, please check back on the 1st.' ) )
        else:
            
            if request_type == HC.GET and request != 'accesskeys': c.execute( 'BEGIN DEFERRED' )
            else: c.execute( 'BEGIN IMMEDIATE' )
            
            try:
                
                service_id = self._GetServiceId( c, service_identifier )
                
                permissions = HC.REQUESTS_TO_PERMISSIONS[ ( service_type, request_type, request ) ]
                
                if permissions is not None or request == 'account':
                    
                    account = self._GetAccount( c, service_id, account_identifier )
                    
                    if permissions is not None: account.CheckPermissions( permissions )
                    
                else: account = None
                
                if request_type == HC.GET: response_context = self._MainLoop_Get( c, request, service_type, service_id, account, request_args )
                else:
                    
                    self._MainLoop_Post( c, request, service_type, service_id, account, ip, request_args )
                    
                    response_context = HC.ResponseContext( 200 )
                    
                
                c.execute( 'COMMIT' )
                
                for ( topic, args, kwargs ) in self._pubsubs: HC.pubsub.pub( topic, *args, **kwargs )
                
                if ( service_type, request_type, request ) in HC.BANDWIDTH_CONSUMING_REQUESTS:
                    
                    if request_type == HC.GET: HC.pubsub.pub( 'request_made', ( service_identifier, account, response_context.GetLength() ) )
                    elif request_type == HC.POST: HC.pubsub.pub( 'request_made', ( service_identifier, account, request_length ) )
                    
                
                job.PutResult( response_context )
                
            except Exception as e:
                
                c.execute( 'ROLLBACK' )
                
                ( exception_type, value, tb ) = sys.exc_info()
                
                new_e = type( e )( os.linesep.join( traceback.format_exception( exception_type, value, tb ) ) )
                
                job.PutResult( new_e )
                
            
        
    
    def _MainLoop_Get( self, c, request, service_type, service_id, account, request_args ):
        
        try: account_id = account.GetAccountId()
        except: pass
        
        if request == 'accesskeys':
            
            num = request_args[ 'num' ]
            title = request_args[ 'title' ]
            expiration = request_args[ 'expiration' ]
            
            account_type_id = self._GetAccountTypeId( c, service_id, title )
            
            access_keys = self._GenerateAccessKeys( c, service_id, num, account_type_id, expiration )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( access_keys ) )
            
        elif request == 'account': response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( account ) )
        elif request == 'accountinfo':
            
            subject_identifier = request_args[ 'subject_identifier' ]
            
            subject = self._GetAccount( c, service_id, subject_identifier )
            
            subject_account_id = subject.GetAccountId()
            
            if service_type == HC.FILE_REPOSITORY: subject_info = self._GetAccountFileInfo( c, service_id, subject_account_id )
            elif service_type == HC.TAG_REPOSITORY: subject_info = self._GetAccountMappingInfo( c, service_id, subject_account_id )
            else: subject_info = {}
            
            subject_info[ 'account' ] = subject
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( subject_info ) )
            
        elif request == 'accounttypes':
            
            account_types = self._GetAccountTypes( c, service_id )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( account_types ) )
            
        elif request == 'file':
            
            hash = request_args[ 'hash' ]
            
            file = self._GetFile( hash )
            
            mime = HC.GetMimeFromString( file )
            
            response_context = HC.ResponseContext( 200, mime = mime, body = file, filename = hash.encode( 'hex' ) + HC.mime_ext_lookup[ mime ] )
            
        elif request == 'init':
            
            if c.execute( 'SELECT 1 FROM account_map WHERE service_id = ?;', ( service_id, ) ).fetchone() is not None: raise HC.ForbiddenException( 'This server is already initialised!' )
            
            account_type_id = self._GetAccountTypeId( c, service_id, 'server admin' )
            
            ( access_key, ) = self._GenerateAccessKeys( c, service_id, 1, account_type_id, None )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( access_key ) )
            
        elif request == 'ip':
            
            hash = request_args[ 'hash' ]
            
            hash_id = self._GetHashId( c, hash )
            
            ( ip, timestamp ) = self._GetIPTimestamp( c, service_id, hash_id )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( ( ip, timestamp ) ) )
            
        elif request == 'message':
            
            message_key = request_args[ 'message_key' ]
            
            message = self._GetMessage( c, service_id, account_id, message_key )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_OCTET_STREAM, body = message )
            
        elif request == 'messageinfosince':
            
            timestamp = request_args[ 'since' ]
            
            ( message_keys, statuses ) = self._GetMessageInfoSince( c, service_id, account_id, timestamp )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( ( message_keys, statuses ) ) )
            
        elif request == 'numpetitions':
            
            if service_type == HC.FILE_REPOSITORY: num_petitions = self._GetNumFilePetitions( c, service_id )
            elif service_type == HC.TAG_REPOSITORY: num_petitions = self._GetNumMappingPetitions( c, service_id )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( num_petitions ) )
            
        elif request == 'options':
            
            options = self._GetOptions( c, service_id )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( options ) )
            
        elif request == 'petition':
            
            if service_type == HC.FILE_REPOSITORY: petition = self._GetFilePetition( c, service_id )
            if service_type == HC.TAG_REPOSITORY: petition = self._GetMappingPetition( c, service_id )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( petition ) )
            
        elif request == 'publickey':
            
            contact_key = request_args[ 'contact_key' ]
            
            public_key = self._GetPublicKey( c, contact_key )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( public_key ) )
            
        elif request == 'services':
            
            service_identifiers = self._GetServiceIdentifiers( c )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( service_identifiers ) )
            
        elif request == 'stats':
            
            stats = self._GetRestrictedServiceStats( c, service_id )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = yaml.safe_dump( stats ) )
            
        elif request == 'thumbnail':
            
            hash = request_args[ 'hash' ]
            
            thumbnail = self._GetThumbnail( hash )
            
            mime = HC.GetMimeFromString( thumbnail )
            
            response_context = HC.ResponseContext( 200, mime = mime, body = thumbnail, filename = hash.encode( 'hex' ) + '_thumbnail' + HC.mime_ext_lookup[ mime ] )
            
        elif request == 'update':
            
            begin = request_args[ 'begin' ]
            
            update = self._GetCachedUpdate( c, service_id, begin )
            
            response_context = HC.ResponseContext( 200, mime = HC.APPLICATION_YAML, body = update ) # note that we don't yaml.safe_dump, because it already is!
            
        
        return response_context
        
    
    def _MainLoop_Post( self, c, request, service_type, service_id, account, ip, request_args ):
        
        try: account_id = account.GetAccountId()
        except: pass
        
        if request == 'accountmodification':
            
            action = request_args[ 'action' ]
            subject_identifiers = request_args[ 'subject_identifiers' ]
            
            admin_account_id = account.GetAccountId()
            
            subjects = [ self._GetAccount( c, service_id, subject_identifier ) for subject_identifier in subject_identifiers ]
            
            subject_account_ids = [ subject.GetAccountId() for subject in subjects ]
            
            if action in ( HC.BAN, HC.SUPERBAN ):
                
                reason = request_args[ 'reason' ]
                
                reason_id = self._GetReasonId( c, reason )
                
                if expiration in request_args: expiration = request_args[ 'expiration' ]
                else: expiration = None
                
                self._Ban( c, service_id, action, admin_account_id, subject_account_ids, reason_id, expiration ) # fold ban and superban together, yo
                
            else:
                
                account.CheckPermissions( HC.GENERAL_ADMIN ) # special case, don't let manage_users people do these:
                
                if action == HC.CHANGE_ACCOUNT_TYPE:
                    
                    title = request_args[ 'title' ]
                    
                    account_type_id = self._GetAccountTypeId( c, service_id, title )
                    
                    self._ChangeAccountType( c, service_id, subject_account_ids, account_type_id )
                    
                elif action == HC.ADD_TO_EXPIRES:
                    
                    expiration = request_args[ 'expiration' ]
                    
                    self._AddToExpires( c, service_id, subject_account_ids, expiration )
                    
                elif action == HC.SET_EXPIRES:
                    
                    expires = request_args[ 'expiry' ]
                    
                    self._SetExpires( c, service_id, subject_account_ids, expires )
                    
                
            
        elif request == 'accounttypesmodification':
            
            edit_log = request_args[ 'edit_log' ]
            
            self._ModifyAccountTypes( c, service_id, edit_log )
            
        elif request == 'backup': self._MakeBackup( c )
        elif request == 'contact':
            
            public_key = request_args[ 'public_key' ]
            
            self._CreateContact( c, service_id, account_id, public_key )
            
        elif request == 'file':
            
            request_args[ 'ip' ] = ip
            
            self._AddFile( c, service_id, account_id, request_args )
            
        elif request == 'mappings':
            
            mappings = request_args[ 'mappings' ]
            
            tags = mappings.GetTags()
            
            hashes = mappings.GetHashes()
            
            self._GenerateTagIdsEfficiently( c, tags )
            
            self._GenerateHashIdsEfficiently( c, hashes )
            
            overwrite_deleted = account.HasPermission( HC.RESOLVE_PETITIONS )
            
            for ( tag, hashes ) in mappings:
                
                tag_id = self._GetTagId( c, tag )
                
                hash_ids = self._GetHashIds( c, hashes )
                
                self._AddMappings( c, service_id, account_id, tag_id, hash_ids, overwrite_deleted )
                
            
        elif request == 'message':
            
            contact_key = request_args[ 'contact_key' ]
            message = request_args[ 'message' ]
            
            self._AddMessage( c, contact_key, message )
            
        elif request == 'message_statuses':
            
            contact_key = request_args[ 'contact_key' ]
            statuses = request_args[ 'statuses' ]
            
            self._AddStatuses( c, contact_key, statuses )
            
        elif request == 'news':
            
            news = request_args[ 'news' ]
            
            self._AddNews( c, service_id, news )
            
        elif request == 'options':
            
            options = request_args[ 'options' ]
            
            self._SetOptions( c, service_id, service_identifier, options )
            
        elif request == 'petitiondenial':
            
            petition_denial = request_args[ 'petition_denial' ]
            
            if service_type == HC.FILE_REPOSITORY:
                
                hashes = petition_denial.GetInfo()
                
                hash_ids = self._GetHashIds( c, hashes )
                
                self._DenyFilePetition( c, service_id, hash_ids )
                
            elif service_type == HC.TAG_REPOSITORY:
                
                ( tag, hashes ) = petition_denial.GetInfo()
                
                tag_id = self._GetTagId( c, tag )
                
                hash_ids = self._GetHashIds( c, hashes )
                
                self._DenyMappingPetition( c, service_id, tag_id, hash_ids )
                
            
        elif request == 'petitions':
            
            petitions = request_args[ 'petitions' ]
            
            if service_type == HC.FILE_REPOSITORY:
                
                if account.HasPermission( HC.RESOLVE_PETITIONS ): petition_method = self._ApproveFilePetition
                elif account.HasPermission( HC.POST_PETITIONS ): petition_method = self._AddFilePetition
                
                for ( reason, hashes ) in petitions:
                    
                    reason_id = self._GetReasonId( c, reason )
                    
                    hash_ids = self._GetHashIds( c, hashes )
                    
                    petition_method( c, service_id, account_id, reason_id, hash_ids )
                    
                
            elif service_type == HC.TAG_REPOSITORY:
                
                if account.HasPermission( HC.RESOLVE_PETITIONS ): petition_method = self._ApproveMappingPetition
                elif account.HasPermission( HC.POST_PETITIONS ): petition_method = self._AddMappingPetition
                
                for ( reason, tag, hashes ) in petitions:
                    
                    reason_id = self._GetReasonId( c, reason )
                    
                    tag_id = self._GetTagId( c, tag )
                    
                    hash_ids = self._GetHashIds( c, hashes )
                    
                    petition_method( c, service_id, account_id, reason_id, tag_id, hash_ids )
                    
                
            
        elif request == 'servicesmodification':
            
            edit_log = request_args[ 'edit_log' ]
            
            self._ModifyServices( c, account_id, edit_log )
            
        
    
    def _MainLoop_Read( self, c, action ):
        
        if action == 'dirty_updates': return self._GetDirtyUpdates( c )
        elif action == 'update_ends': return self._GetUpdateEnds( c )
        else: raise Exception( 'db received an unknown read command: ' + action )
        
    
    def _MainLoop_Write( self, c, action, args ):
        
        if action == 'check_data_usage': self._CheckDataUsage( c )
        elif action == 'check_monthly_data': self._CheckMonthlyData( c )
        elif action == 'clear_bans': self._ClearBans( c )
        elif action == 'delete_orphans': self._DeleteOrphans( c )
        elif action == 'flush_requests_made': self._FlushRequestsMade( c, *args )
        elif action == 'clean_update': self._CleanUpdate( c, *args )
        elif action == 'create_update': self._CreateUpdate( c, *args )
        else: raise Exception( 'db received an unknown write command: ' + action )
        
    
    def MainLoop( self ):
        
        ( db, c ) = self._GetDBCursor()
        
        while not HC.shutdown or not self._jobs.empty():
            
            try:
                
                ( priority, job ) = self._jobs.get( timeout = 1 )
                
                self._pubsubs = []
                
                try:
                    
                    if isinstance( job, HC.JobServer ): self._MainLoop_JobServer( c, job )
                    else: self._MainLoop_JobInternal( c, job )
                    
                except:
                    
                    self._jobs.put( ( priority, job ) ) # couldn't lock db; put job back on queue
                    
                    time.sleep( 5 )
                    
                
            except: pass # no jobs this second; let's see if we should shutdown
            
        
    
    def Read( self, action, priority, *args, **kwargs ):
        
        job_type = 'read'
        
        job = HC.JobInternal( action, job_type, *args, **kwargs )
        
        self._jobs.put( ( priority + 1, job ) ) # +1 so all writes of equal priority can clear out first
        
        if action != 'do_query': return job.GetResult()
        
    
    def Write( self, action, priority, *args, **kwargs ):
        
        job_type = 'write'
        
        job = HC.JobInternal( action, job_type, *args, **kwargs )
        
        self._jobs.put( ( priority, job ) )
        
    