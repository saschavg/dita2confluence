#!/usr/bin/env python

prog_description='''
    This script uploads dita generated xhtml to confluence. It must be provided with the index.html containing the table of contents. 
    All files including the toc it self will be uploaded to confluence preserving the structure of the TOC. 
    it converts links and images to the confluence storage format. Links are changed to work in confluence. Images are uploaded as
    attachements to the relevant pages. 
    The tile of a page is used as the identifier for the pages in confluence. When uploading, existing pages with the same title will
    be overwritten. Conflence will keep the old version. Also comments on the pages will be preserved.
'''

import socket
import sys, getopt, argparse
import re
import os
import mimetypes 
import pprint
import xmlrpclib, urllib2
import binascii
from xml.dom import minidom

pp = pprint.PrettyPrinter(indent=2)

DO_UPLOAD=False

class Urllib2Transport(xmlrpclib.Transport):
    def __init__(self, opener=None, https=False, use_datetime=0):
        xmlrpclib.Transport.__init__(self, use_datetime)
        self.opener = opener or urllib2.build_opener()
        self.https = https
    
    def request(self, host, handler, request_body, verbose=0):
        self.verbose = verbose
        proto = ('http', 'https')[bool(self.https)]
        req = urllib2.Request('%s://%s%s' % (proto, host, handler), request_body)
        req.add_header('Content-Length', str(len(request_body)))
        req.add_header('Content-Type', "text/xml")
        req.add_header('User-agent', self.user_agent)
        if self.verbose:
            print req.get_full_url()
            print req.header_items()
            print req.get_data()
        resp = self.opener.open(req)
        return self.parse_response(resp)

 
class HTTPProxyTransport(Urllib2Transport):
    def __init__(self, proxies, use_datetime=0):
        #Urllib2Transport.__init__(self, None, use_datetime)
        opener = urllib2.build_opener(urllib2.ProxyHandler(proxies))
        Urllib2Transport.__init__(self, opener, use_datetime)

def fetchImages(xml, rel_basedir):
    images = xml.getElementsByTagName('img')
    a_images = []
    for img in images :
        a_images.append({
            "path" : os.path.abspath(rel_basedir + "/" + img.getAttribute('src')),
            "name" : os.path.basename(img.getAttribute('src')),
        })
        
        acAttach = xml.createElement('ri:attachment')
        acAttach.setAttribute('ri:filename', a_images[-1]['name'])
        acImg = xml.createElement('ac:image')
        acImg.appendChild(acAttach)
        img.parentNode.replaceChild(acImg, img)
    return a_images

def updateLinks(xml):

    links = xml.getElementsByTagName('a')
    for link in links:
        title = ''.join([t.nodeValue for t in link.childNodes])

        #remove any line breaks that might have been introduced by tidy
        s = re.compile('\s+')
        title = s.sub(' ', title)

        acLink= xml.createElement('ac:link')
        riPage = xml.createElement('ri:page')
        riPage.setAttribute('ri:content-title', title)
        acPTLB= xml.createElement('ac:plain-text-link-body')
        cdata = xml.createCDATASection(title)
        acPTLB.appendChild(cdata)
        acLink.appendChild(riPage)
        acLink.appendChild(acPTLB)
        link.parentNode.replaceChild(acLink, link)
        
def fetchTitle(xml):
    '''
    try to identify the title from the given xml fragment.
    first it will try to find the title in a meta element with name attribute "DC.Title"
    if not found, it will try to find a "title" element and use its contents as the title
    if still not found it will check if the given xml is an anchor tag. If so, it will use its contents as the title.
    if no matches were found, it returns "None"
    '''
    title=None
    metaEls = xml.getElementsByTagName('meta')
    for el in metaEls :
        name = el.getAttribute('name') 
        if name == 'DC.Title' : 
            title = el.getAttribute('content') 
            break
    if title == None:
        tn = xml.getElementsByTagName('title')
        if len(tn) > 0:
            title = ''.join([t.nodeValue for t in tn[0].childNodes])
    if title == None and xml.tagName=='a':
        title = ''.join([t.nodeValue for t in xml.childNodes])

    #remove any line breaks that might have been introduced by tidy
    s = re.compile('\s+')
    title = s.sub(' ', title)
    return title.strip()

def removePages(rpc_service, token, pages):
    for page in pages:
        if DO_UPLOAD:
            print "delete page : " + page['title']
            rpc_service.confluence2.removePage(token,page.get('id'))
        else:
            print "simulate: delete page : " + page['title']

def uploadImages(service, images, pageId):
    for img in images :

        with open(img['path'],'rb') as f:
            data = f.read()

        attachement = {}
        attachement['fileName'] = "carwash.jpg"
        #attachement['fileSize'] = len(data) 
        attachement['contentType'] = mimetypes.guess_type(img['path'])[0]
        attach = {}
        if DO_UPLOAD:
            attach = service.confluence2.addAttachment(token, pageId, attachement, xmlrpclib.Binary(data))
        return attach

def filter_decendant_pages(root_page, pages):
    filtered_pages = []
    for page in pages:

        parentId = page['parentId']

        while parentId != None and parentId != root_page['id'] :
            parentId = next( (p['parentId'] for p in pages if p['id'] == parentId), None)
        
        if parentId != None :
            filtered_pages.append(page)
                
    return filtered_pages

def fetch_space_home_page(space, current_pages):
    r = [p for p in current_pages if p['id'] == space['homePage']]
    if len(r) ==  0:
        print "error: home page not found space "
        sys.exit(1)
    return r[0]

def storePage(html_file, parent_page, current_pages, rpc_service, token):

    print "\nstoring page: " + html_file
    rel_basedir = os.path.dirname(html_file)
    xml_doc     = minidom.parse(html_file)
    title       = fetchTitle(xml_doc)
    print title;
    images      = fetchImages(xml_doc, rel_basedir)
    updateLinks(xml_doc) 
    content     = xml_doc.getElementsByTagName('body')[0]

    #remove title from HTML as confluence already creates a title for each page
    h1_title = content.getElementsByTagName('h1')
    if len(h1_title):
            content.removeChild(h1_title[0])

    page = {}

    #check if page already exists and update in that case
    r = [p for p in current_pages if p['title'] == title]
    if len(r) > 0 :
        print "updating existing page: " + title
        page = r[0]
    else : 
        print "creating new page: " + title

    #set page properties
    page['title']   = title
    page['content'] = content.toxml() 
    page['space']   = parent_page['space'] 
    page['parentId']= parent_page['id'] 

    #uploading the page
    print "uploading page"
    if DO_UPLOAD:
        page = rpc_service.confluence2.storePage(token, page)
    else:
        #dummy page object
        page = {"id":"0", "parentId":"0", "space": "TEST_TEST" }

    print "id: "+ page['id'] 
    print "parentId : "+ page['parentId'] 

    if len(images) > 0 :
        print "uploading images"
    else:
        print "no images found for upload"
    uploadImages(rpc_service, images,page.get('id'))

    #pp.pprint(page)
    print "upload complete"
    print "---------------------------------------------------------------------"
    return page

def parse_toc(toc_file, rel_basedir):
    '''
    read the html file containing the table of contents and produce a nested list of dictionaries 
    repesenting the TOC structure. Each dictionary in the list holds a reference to the html file
    and the tile of that file.

    below a representation of the structure produced:

    [ 
        {
            children: [
               { 
                    children : [{}]
                    links : [{}]

               },
               ...
            ],
            links: [
                {
                    path : #absolute path to the original HTML file referenced in the index. this is the href of the anchor tag
                    title: # title of the file. this is the contents of the anchor tag 
                },
                ...
            ]

        },
        ...
    ]
    '''
    xml= minidom.parse(toc_file)
    title = fetchTitle(xml)
    body = xml.getElementsByTagName('body')[0]
    flat_toc=[]

    def get_toc(node, toc):

        for child in node.childNodes:
            if child.nodeType == child.ELEMENT_NODE :
                new_toc = None

                if child.nodeName== 'li' :
                    new_toc= {'children': [], "links":[] }
                    toc['children'].append(new_toc)

                if child.nodeName == 'a' :
                    link={}
                    link['path']     = os.path.abspath( rel_basedir + "/" + child.getAttribute('href'))
                    link['title']    = fetchTitle(child)
                    toc['links'].append(link)
                    flat_toc.append(link)

                get_toc(child, new_toc or toc )
        return toc

    link = {"path":toc_file, "title":title}
    flat_toc.append(link)
    toc = {'children': [], "links":[link] }
    toc = get_toc(body,toc)
    toc["flat_toc"] = flat_toc 
    return toc 

def gen_pages(toc, space, parent_page, **kwargs):

    if len(toc['links']):
        path = toc['links'][0]['path']
        page = storePage(path, parent_page=parent_page, **kwargs)
    else:
        page = parent_page

    toc['page'] = page

    for child in toc['children']:
        gen_pages(child, space+"    ", parent_page=page, **kwargs)
    
    if len(toc['children']) > 0:
        # order pages in line with the TOC
        token = kwargs['token']
        for i,child in enumerate(toc['children']):
            if i==0 : continue
            pageId = child['page']['id']
            targetId = toc['children'][i-1]['page']['id']
            res = service.confluence2.movePage(token, pageId, targetId, 'below')

def printToc(toc, space=""):
    if len(toc['links']) :
        path = toc['links'][0]['path']
        print space+path 
    else:
        # this happens when a branch in de index.html file does not contain a link. I.e. a LI tag that does not
        # contain an A tag as a direct child.
        print space+ "ERROR: found a child in the index.html which is not a link and has no corresponding page"
    for child in toc['children']:
        printToc(child, space+"   ")

def find_obsolete_pages(applicable_pages, toc):
    titles = [p['title'].lower() for p in toc["flat_toc"]]
    obsolete_pages = [p for p in applicable_pages if p['title'].lower() not in titles]
    return obsolete_pages

if __name__ == "__main__":

    #set to False to run the script but do not actually upload any files
    DO_UPLOAD=True

    # generic input properties

    parser = argparse.ArgumentParser(description=prog_description)
    parser.add_argument('-d', dest='delete_obsolete_pages', action='store_true', default=False, help='delete obsolete pages under given root page. see "-r option"')
    parser.add_argument('-u', dest='confluence_user', required=True, help='The confluence user used to upload')
    parser.add_argument('-p', dest='confluence_pass', required=True, help='Password of the confluence user')
    parser.add_argument('-s', dest='confluence_space', required=True, help='name of the confluence space')
    parser.add_argument('-r', dest='confluence_root_page', required=True, help='this is the title of the page under which the files should be uploaded')
    parser.add_argument('--proxy', dest='proxy', default=None, help='configure proxy: http://user:pass@host:port')
    parser.add_argument('--url', dest='confluence_rpc_url', required=True, help='url of the confluence rpc service: "https://CONFLUENCE_HOST/rpc/xmlrpc"')
    parser.add_argument('toc_file', help='this is the index.html file containig the table of contents')
    args = parser.parse_args()

    args.toc_file = os.path.abspath(args.toc_file)
    if not os.path.isfile(args.toc_file):
        print 'given file "'+args.toc_file+'" does not exist' 
        parser.print_help()
        exit(2)

    # Absolute path to the docroot containing the html files
    basedir = os.path.dirname(args.toc_file)

    # Space key of the space where the files need to be uploaded
    #confluence_space= "DOC3"

    toc = parse_toc(args.toc_file, basedir)
    
    if args.proxy:
        transport = HTTPProxyTransport({'http':args.proxy})
        service     = xmlrpclib.Server(  args.confluence_rpc_url, verbose=0, transport=transport)
    else:
        service     = xmlrpclib.Server(  args.confluence_rpc_url, verbose=0)

    token       = service.confluence2.login(args.confluence_user, args.confluence_pass)
    # fetch space information
    space       = service.confluence2.getSpace(token,args.confluence_space)

    # fetch all pages in a space 
    pages       = service.confluence2.getPages(token,args.confluence_space)

    # identify the home page of the space 
    #home_page   = fetch_space_home_page(space, pages) 

    # identify the root page to use
    root_page=None
    for p in pages :
        if p["title"] == args.confluence_root_page:
            root_page = p

    if not root_page or not args.confluence_root_page:
        print "Error: root '"+args.confluence_root_page+"'page not found"
        sys.exit(2)
    toc['page'] = root_page;

    print "\n#################### TOC ####################### TOC ######################\n"
    printToc(toc)
    print "---------------------------------------------------------------------------"

    #identify all pages that are decendants of the root page
    applicable_pages = filter_decendant_pages(root_page, pages)

    obsolete_pages = find_obsolete_pages(applicable_pages, toc)
    if len(obsolete_pages) > 0:
        print "Found obsolete pages under the given root:"
        for p in obsolete_pages: print "   -  "+p['title']
    
        if args.delete_obsolete_pages:
            removePages(service, token, obsolete_pages)
            print "deleted obsolete pages"

    # upload the pages. We wil only override pages that are decendants of the given root page. If a page
    # with the same title already exists outside this tree, confluence will throw an error.
    gen_pages(toc, space="", parent_page=root_page, current_pages=applicable_pages, rpc_service=service, token=token)
