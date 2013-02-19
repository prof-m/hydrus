import bs4
import lxml
import traceback
import urlparse

def Parse4chanPostScreen( html ):
    
    soup = bs4.BeautifulSoup( html )
    
    title_tag = soup.find( 'title' )
    
    if title_tag.string == 'Post successful!': return ( 'success', None )
    else:
        
        try:
            
            problem_tag = soup.find( id = 'errmsg' )
            
            if problem_tag is None:
                
                try: print( soup )
                except: pass
                
                return ( 'error', 'unknown problem, writing 4chan html to log' )
                
            
            problem = str( problem_tag )
            
            if 'CAPTCHA' in problem: return ( 'captcha', None )
            elif 'seconds' in problem: return ( 'too quick', None )
            elif 'Duplicate' in problem: return ( 'error', 'duplicate file detected' )
            else: return ( 'error', problem )
            
        except: return ( 'error', 'unknown error' )
        
    
def ParseBooruGallery( html, url_base, thumb_classname ):
    
    urls_set = set()
    urls = []
    
    soup = bs4.BeautifulSoup( html )
    
    thumbnails = soup.find_all( class_ = thumb_classname )
    
    for thumbnail in thumbnails:
        
        links = thumbnail.find_all( 'a' )
        
        if thumbnail.name == 'a': links.append( thumbnail )
        
        for link in links:
            
            if link.string is not None and link.string == 'Image Only': continue # rule 34 @ paheal fix
            
            url = link[ 'href' ]
            
            url = urlparse.urljoin( url_base, url )
            
            if url not in urls_set:
                
                urls_set.add( url )
                urls.append( url )
                
            
        
    
    return urls
    
def ParseBooruPage( html, url_base, tag_classnames_to_namespaces, image_id = None, image_data = None ):
    
    soup = bs4.BeautifulSoup( html )
    
    image_base = None
    
    if image_id is not None:
        
        image = soup.find( id = image_id )
        
        image_url = image[ 'src' ]
        
    
    if image_data is not None:
        
        links = soup.find_all( 'a' )
        
        for link in links:
            
            if link.string == image_data: image_url = link[ 'href' ]
            
        
    
    image_url = urlparse.urljoin( url_base, image_url )
    
    image_url = image_url.replace( 'sample/sample-', '' ) # fix for danbooru resizing
    
    tags = []
    
    for ( tag_classname, namespace ) in tag_classnames_to_namespaces.items():
        
        tag_list_entries = soup.find_all( class_ = tag_classname )
        
        for tag_list_entry in tag_list_entries:
            
            links = tag_list_entry.find_all( 'a' )
            
            if tag_list_entry.name == 'a': links.append( tag_list_entry )
            
            for link in links:
                
                if link.string not in ( '?', '-', '+' ):
                    
                    if namespace == '': tags.append( link.string )
                    else: tags.append( namespace + ':' + link.string )
                    
                
            
        
    
    return ( image_url, tags )
    
def ParseDeviantArtGallery( html ):
    
    results = []
    
    soup = bs4.BeautifulSoup( html )
    
    thumbs_container = soup.find( class_ = 'stream stream-fh' )
    
    def starts_with_thumb( classname ): return classname is not None and classname.startswith( 'thumb' )
    
    links = thumbs_container.find_all( 'a', class_ = starts_with_thumb )
    
    for link in links:
        
        page_url = link[ 'href' ] # something in the form of blah.da.com/art/blah-123456
        
        page_url_split = page_url.split( '-' )
        
        deviant_art_file_id = page_url_split[-1 ]
        
        image_url = 'http://www.deviantart.com/download/' + deviant_art_file_id + '/' # trailing slash is important
        
        raw_title = link[ 'title' ] # something in the form sweet dolls by ~AngeniaC, Feb 29, 2012 in Artisan Crafts &gt; Miniatures &gt; Jewelry
        
        tags = []
        
        ( title, raw_title ) = raw_title.split( ' by ~', 1 )
        
        ( creator, raw_title ) = raw_title.split( ', ', 1 )
        
        ( date_gumpf, raw_category_tags ) = raw_title.split( ' in ', 1 )
        
        category_tags = raw_category_tags.split( ' > ' )
        
        tags = []
        
        tags.append( 'title:' + title )
        tags.append( 'creator:' + creator )
        tags.extend( category_tags )
        
        results.append( ( image_url, tags ) )
        
    
    return results
    
def ParsePage( html, url ):
    
    soup = bs4.BeautifulSoup( html )
    
    all_links = soup.find_all( 'a' )
    
    links_with_images = [ link for link in all_links if len( link.find_all( 'img' ) ) > 0 ]
    
    urls = [ urlparse.urljoin( url, link[ 'href' ] ) for link in links_with_images ]
    
    # old version included (images that don't have a link wrapped around them)'s src
    
    return urls
    
def ParseHentaiFoundryGallery( html ):
    
    urls_set = set()
    
    soup = bs4.BeautifulSoup( html )
    
    def correct_url( href ):
        
        # a good url is in the form "/pictures/user/artist_name/file_id/title"
        
        if href.count( '/' ) == 5 and href.startswith( '/pictures/user/' ):
            
            ( nothing, pictures, user, artist_name, file_id, title ) = href.split( '/' )
            
            # /pictures/user/artist_name/page/3
            if file_id != 'page': return True
            
        
        return False
        
    
    links = soup.find_all( 'a', href = correct_url )
    
    urls = [ 'http://www.hentai-foundry.com' + link['href'] for link in links ]
    
    result_urls = []
    
    for url in urls:
        
        if url not in urls_set:
            
            urls_set.add( url )
            
            result_urls.append( url )
            
        
    
    return result_urls
    
def ParseHentaiFoundryPage( html ):
    
    # can't parse this easily normally because HF is a pain with the preview->click to see full size business.
    # find http://pictures.hentai-foundry.com//
    # then extend it to http://pictures.hentai-foundry.com//k/KABOS/172144.jpg
    # the .jpg bit is what we really need, but whatever
    try:
        
        index = html.index( 'http://pictures.hentai-foundry.com//' )
        
        stuff = html[ index : index + 100 ]
        
        try: ( image_url, gumpf ) = stuff.split( '"', 1 )
        except: ( image_url, gumpf ) = stuff.split( '&#039;', 1 )
        
    except: raise Exception( 'Could not parse image url!' )
    
    soup = bs4.BeautifulSoup( html )
    
    tags = []
    
    try:
        
        title = soup.find( 'title' )
        
        ( data, nothing ) = unicode( title.string ).split( ' - Hentai Foundry' )
        
        data_reversed = data[::-1] # want to do it right-side first, because title might have ' by ' in it
        
        ( artist_reversed, title_reversed ) = data_reversed.split( ' yb ' )
        
        artist = artist_reversed[::-1]
        
        title = title_reversed[::-1]
        
        tags.append( 'creator:' + artist )
        tags.append( 'title:' + title )
        
    except: pass
    
    tag_links = soup.find_all( 'a', rel = 'tag' )
    
    for tag_link in tag_links: tags.append( tag_link.string )
    
    return ( image_url, tags )
    