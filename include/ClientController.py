import gc
import HydrusConstants as HC
import HydrusImageHandling
import ClientConstants as CC
import ClientDB
import ClientGUI
import os
import threading
import time
import traceback
import wx
import wx.richtext

ID_ANIMATED_EVENT_TIMER = wx.NewId()
ID_MAINTENANCE_EVENT_TIMER = wx.NewId()

class Controller( wx.App ):
    
    def ClearCaches( self ):
        
        self._thumbnail_cache.Clear()
        self._fullscreen_image_cache.Clear()
        self._preview_image_cache.Clear()
        
    
    def Clipboard( self, type, data ):
        
        # need this cause can't do it in a non-gui thread
        
        if type == 'paths':
            
            paths = data
            
            if wx.TheClipboard.Open():
                
                data = wx.FileDataObject()
                
                for path in paths: data.AddFile( path )
                
                wx.TheClipboard.SetData( data )
                
                wx.TheClipboard.Close()
                
            else: raise Exception( 'Could not get permission to access the clipboard!' )
            
        
    
    def EventAnimatedTimer( self, event ):
        
        del gc.garbage[:]
        
        HC.pubsub.pub( 'animated_tick' )
        
    
    def EventMaintenanceTimer( self, event ):
        
        if int( time.time() ) - self._last_idle_time > 20 * 60: # 20 mins since last user-initiated db request
            
            self.MaintainDB()
            
        
    
    def EventPubSub( self, event ):
        
        pubsubs_queue = HC.pubsub.GetQueue()
        
        ( callable, args, kwargs ) = pubsubs_queue.get()
        
        try: callable( *args, **kwargs )
        except wx._core.PyDeadObjectError: pass
        except TypeError: pass
        except Exception as e:
            
            print( type( e ) )
            print( traceback.format_exc() )
            
        
        pubsubs_queue.task_done()
        
    
    def Exception( self, exception ): wx.MessageBox( unicode( exception ) )
    
    def GetFullscreenImageCache( self ): return self._fullscreen_image_cache
    
    def GetGUI( self ): return self._gui
    
    def GetLog( self ): return self._log
    
    def GetPreviewImageCache( self ): return self._preview_image_cache
    
    def GetThumbnailCache( self ): return self._thumbnail_cache
    
    def MaintainDB( self ):
        
        now = int( time.time() )
        
        shutdown_timestamps = self.Read( 'shutdown_timestamps' )
        
        if now - shutdown_timestamps[ CC.SHUTDOWN_TIMESTAMP_VACUUM ] > 86400 * 5: self.Write( 'vacuum' )
        if now - shutdown_timestamps[ CC.SHUTDOWN_TIMESTAMP_FATTEN_AC_CACHE ] > 50000: self.Write( 'fatten_autocomplete_cache' )
        if now - shutdown_timestamps[ CC.SHUTDOWN_TIMESTAMP_DELETE_ORPHANS ] > 86400 * 3: self.Write( 'delete_orphans' )
        
    
    def Message( self, message ): wx.MessageBox( message )
    
    def OnInit( self ):
        
        try:
            
            self._splash = ClientGUI.FrameSplash()
            
            self.SetSplashText( 'log' )
            
            self._log = CC.Log()
            
            self.SetSplashText( 'db' )
            
            self._db = ClientDB.DB()
            
            self._options = self._db.Read( 'options', HC.HIGH_PRIORITY )
            
            self._tag_service_precedence = self._db.Read( 'tag_service_precedence', HC.HIGH_PRIORITY )
            
            self.SetSplashText( 'caches' )
            
            self._fullscreen_image_cache = CC.RenderedImageCache( self._db, self._options, 'fullscreen' )
            self._preview_image_cache = CC.RenderedImageCache( self._db, self._options, 'preview' )
            
            self._thumbnail_cache = CC.ThumbnailCache( self._db, self._options )
            
            CC.GlobalBMPs.STATICInitialise()
            
            self.SetSplashText( 'gui' )
            
            self._gui = ClientGUI.FrameGUI()
            
            HC.pubsub.sub( self, 'Exception', 'exception' )
            HC.pubsub.sub( self, 'Message', 'message' )
            HC.pubsub.sub( self, 'Clipboard', 'clipboard' )
            
            self.Bind( HC.EVT_PUBSUB, self.EventPubSub )
            
            # this is because of some bug in wx C++ that doesn't add these by default
            wx.richtext.RichTextBuffer.AddHandler( wx.richtext.RichTextHTMLHandler() )
            wx.richtext.RichTextBuffer.AddHandler( wx.richtext.RichTextXMLHandler() )
            
            self.Bind( wx.EVT_TIMER, self.EventAnimatedTimer, id = ID_ANIMATED_EVENT_TIMER )
            
            self._animated_event_timer = wx.Timer( self, ID_ANIMATED_EVENT_TIMER )
            self._animated_event_timer.Start( 1000, wx.TIMER_CONTINUOUS )
            
            self.SetSplashText( 'starting daemons' )
            
            self._db._InitPostGUI()
            
            self._last_idle_time = 0.0
            
            self.Bind( wx.EVT_TIMER, self.EventMaintenanceTimer, id = ID_MAINTENANCE_EVENT_TIMER )
            
            self._maintenance_event_timer = wx.Timer( self, ID_MAINTENANCE_EVENT_TIMER )
            self._maintenance_event_timer.Start( 20 * 60000, wx.TIMER_CONTINUOUS )
            
        except HC.PermissionException as e: pass
        except:
            
            wx.MessageBox( 'Woah, bad error:' + os.linesep + os.linesep + traceback.format_exc() )
            
            try: self._splash.Close()
            except: pass
            
            return False
            
        
        self._splash.Close()
        
        return True
        
    
    def PrepStringForDisplay( self, text ):
        
        if self._options[ 'gui_capitalisation' ]: return text
        else: return text.lower()
        
    
    def ProcessServerRequest( self, *args, **kwargs ): return self._db.ProcessRequest( *args, **kwargs )
    
    def Read( self, action, *args, **kwargs ):
        
        self._last_idle_time = int( time.time() )
        
        if action == 'options': return self._options
        elif action == 'tag_service_precedence': return self._tag_service_precedence
        elif action == 'file': return self._db.ReadFile( *args, **kwargs )
        elif action == 'thumbnail': return self._db.ReadThumbnail( *args, **kwargs )
        else: return self._db.Read( action, HC.HIGH_PRIORITY, *args, **kwargs )
        
    
    def SetSplashText( self, text ):
        
        self._splash.SetText( text )
        self.Yield() # this processes the event queue immediately, so the paint event can occur
        
    
    def WaitUntilGoodTimeToUseGUIThread( self ):
        
        pubsubs_queue = HC.pubsub.GetQueue()
        
        while True:
            
            if HC.shutdown: raise Exception( 'Client shutting down!' )
            elif pubsubs_queue.qsize() == 0: return
            else: time.sleep( 0.04 )
            
        
    
    def Write( self, action, *args, **kwargs ):
        
        self._last_idle_time = int( time.time() )
        
        self._db.Write( action, HC.HIGH_PRIORITY, *args, **kwargs )
        
    
    def WriteLowPriority( self, action, *args, **kwargs ):
        
        self._db.Write( action, HC.LOW_PRIORITY, *args, **kwargs )
        
    