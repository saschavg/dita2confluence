#!/usr/local/bin/python

prog_description='''
    This script uploads dita generated xhtml to confluence. It must be provided with the index.html containing the table of contents. 
    All files including the toc it self will be uploaded to confluence preserving the structure of the TOC. 
    it converts links and images to the confluence storage format. Links are changed to work in confluence. Images are uploaded as
    attachements to the relevant pages. 
    The tile of a page is used as the identifier for the pages in confluence. When uploading, existing pages with the same title will
    be overwritten. Conflence will keep the old version. Also comments on the pages will be preserved.
'''

import sys, getopt, argparse
import re
import os 
import mimetypes 
import pprint
import xmlrpclib
import binascii
from xml.dom import minidom

pp = pprint.PrettyPrinter(indent=2)

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
    title=None
    metaEls = xml.getElementsByTagName('meta')
    for el in metaEls :
        name = el.getAttribute('name') 
        if name == 'DC.Title' : 
            title = el.getAttribute('content') 
            break
    if title == None:
        tn = xml.getElementsByTagName('title')
        title = ''.join([t.nodeValue for t in tn[0].childNodes])

    return title

def removePages(rpc_service, token, pages):
    for page in pages:
        print "delete page : " + page['title']
        rpc_service.confluence2.removePage(token,page.get('id'))

def uploadImages(service, images, pageId):
    for img in images :

        with open(img['path'],'rb') as f:
            data = f.read()

        attachement = {}
        attachement['fileName'] = "carwash.jpg"
        #attachement['fileSize'] = len(data) 
        attachement['contentType'] = mimetypes.guess_type(img['path'])[0]
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
    content     = xml_doc.getElementsByTagName('body')[0].toxml()

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
    page['content'] = content 
    page['space']   = parent_page['space'] 
    page['parentId']= parent_page['id'] 

    #uploading the page
    print "uploading page"
    page = rpc_service.confluence2.storePage(token, page)

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
    body = xml.getElementsByTagName('body')[0]

    def get_toc(node, toc):

        for child in node.childNodes:
            if child.nodeType == child.ELEMENT_NODE :
                new_toc = None

                if child.getAttribute('class') == "topicref" : 
                    new_toc= {'children': [], "links":[] }
                    toc['children'].append(new_toc)

                if child.nodeName == 'a' :
                    link={}
                    link['path']     = os.path.abspath( rel_basedir + "/" + child.getAttribute('href'))
                    link['title']    = child.nodeValue
                    toc['links'].append(link)

                get_toc(child, new_toc or toc )
        return toc


    toc = {'children': [], "links":[ {"path":toc_file} ] }
    return get_toc(body,toc)

def gen_pages(toc, space, parent_page, **kwargs):

    path = toc['links'][0]['path']
    page = storePage(path, parent_page=parent_page, **kwargs)
    for child in toc['children']:
        gen_pages(child, space+"    ", parent_page=page, **kwargs)

def printToc(toc, space=""):
    path = toc['links'][0]['path']
    print space+path 
    for child in toc['children']:
        printToc(child, space+"   ")

if __name__ == "__main__":

    # generic input properties

    parser = argparse.ArgumentParser(description=prog_description)
    parser.add_argument('-u', dest='confluence_user', required=True, help='The confluence user used to upload')
    parser.add_argument('-p', dest='confluence_pass', required=True, help='Password of the confluence user')
    parser.add_argument('-s', dest='confluence_space', required=True, help='name of the confluence space')
    parser.add_argument('-r', dest='confluence_root_page', required=True, help='this is the title of the page under which the files should be uploaded')
    parser.add_argument('--url', dest='confluence_rpc_url', required=True, help='url of the confluence rpc service: "https://CONFLUENCE_HOST/rpc/xmlrpc"')
    parser.add_argument('toc_file', help='this is the index.html file containig the table of contents')
    args = parser.parse_args()

    if not os.path.isfile(args.toc_file):
        print 'given file "'+args.toc_file+'" does not exist' 
        parser.print_help()
        exit(2)

    # Absolute path to the docroot containing the html files
    basedir = os.path.dirname(args.toc_file)

    # Space key of the space where the files need to be uploaded
    #confluence_space= "DOC3"

    toc = parse_toc(args.toc_file, basedir)

    # generic properties
    service     = xmlrpclib.Server(  args.confluence_rpc_url )
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

    #identify all pages that are decendants of the home page
    #applicable_pages = filter_decendant_pages(root_page, pages)

    #removePages(service, token, pages)

    # upload the pages
    gen_pages(toc, space="", parent_page=root_page, current_pages=pages, rpc_service=service, token=token)
    #pp.pprint(toc)
