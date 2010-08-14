# -*- coding: utf-8 -*-
"""
Parser's execution context (a.k.a. state) object and handling. The state includes:

  - language, retrieved from C{@xml:lang}
  - URI base, determined by <base> (or set explicitly). This is a little bit superfluous, because the current RDFa syntax does not make use of C{@xml:base}; ie, this could be a global value.  But the structure is prepared to add C{@xml:base} easily, if needed.
  - options, in the form of an L{Options<pyRdfa.Options>} instance
  - a separate vocabulary/CURIE handling resource, in the form of an L{Curie<pyRdfa.CURIE>} instance

The execution context object is also used to handle URI-s, CURIE-s, terms, etc.

@summary: RDFa parser execution context
@requires: U{RDFLib package<http://rdflib.net>}
@organization: U{World Wide Web Consortium<http://www.w3.org>}
@author: U{Ivan Herman<a href="http://www.w3.org/People/Ivan/">}
@license: This software is available for use under the
U{W3C® SOFTWARE NOTICE AND LICENSE<href="http://www.w3.org/Consortium/Legal/2002/copyright-software-20021231">}
"""

"""
$Id: State.py,v 1.20 2010-08-14 06:13:33 ivan Exp $
$Date: 2010-08-14 06:13:33 $
"""

import rdflib
from rdflib	import URIRef
from rdflib	import Literal
from rdflib	import BNode
from rdflib	import Namespace
if rdflib.__version__ >= "3.0.0" :
	from rdflib	import RDF  as ns_rdf
	from rdflib	import RDFS as ns_rdfs
else :
	from rdflib.RDFS	import RDFSNS as ns_rdfs
	from rdflib.RDF		import RDFNS  as ns_rdf

from pyRdfa.Options		import Options
from pyRdfa.Utils 		import quote_URI, HostLanguage
from pyRdfa.Curie		import Curie
from pyRdfa				import UnresolvablePrefix, UnresolvableTerm

import re
import random
import urlparse
import urllib

RDFa_1_0_VERSION    = "XHTML+RDFa 1.0"


#: list of 'usual' URI schemes; if a URI does not fall into these, a warning may be issued (can be the source of a bug)
usual_schemes = ["doi", "file", "ftp", "gopher", "hdl", "http", "https", "imap", "isbn", "ldap", "lsid",
				 "mailto", "mms", "mstp", "news", "nntp", "prospero", "rsync", "rtmp", "rtsp", "rtspu", "sftp",
				 "shttp", "sip", "sips", "snews", "stp", "svn", "svn+ssh", "telnet", "tel", "urn", "wais"
				]

#### Core Class definition
class ExecutionContext :
	"""State at a specific node, including the current set of namespaces in the RDFLib sense, current language,
	the base, vocabularies, etc. The class is also used to interpret URI-s and CURIE-s to produce
	URI references for RDFLib.
	
	@ivar options: reference to the overall options
	@type options: L{Options.Options}
	@ivar base: the 'base' URI
	@ivar defaultNS: default namespace
	@ivar lang: language tag (possibly None)
	@ivar curie: vocabulary management class instance
	@type curie: L{Curie.Curie}
	@ivar node: the node to which this state belongs
	@type node: DOM node instance
	@ivar rdfa_version: RDFa version of the content
	@type rdfa_version: String
	"""

	#: list of attributes that allow for lists of values and should be treated as such	
	_list = [ "profile", "rel", "rev", "property", "typeof" ]
	#: mapping table from attribute name to the exact method to retrieve the URI(s).
	_resource_type = {}
	
	def __init__(self, node, graph, inherited_state=None, base="", options=None) :
		"""
		@param node: the current DOM Node
		@param graph: the RDFLib Graph
		@keyword inherited_state: the state as inherited
		from upper layers. This inherited_state is mixed with the state information
		retrieved from the current node.
		@type inherited_state: L{State.ExecutionContext}
		@keyword base: string denoting the base URI for the specific node. This overrides the possible
		base inherited from the upper layers. The 
		current XHTML+RDFa syntax does not allow the usage of C{@xml:base}, but SVG1.2 does, so this is
		necessary for SVG (and other possible XML dialects that accept C{@xml:base})
		@keyword options: invocation options, and references to warning graphs
		@type options: L{Options<pyRdfa.Options>}
		"""
		# This additional class initialization that must be done run time, otherwise import errors show up
		if len(	ExecutionContext._resource_type ) == 0 :	
			ExecutionContext._resource_type = {
				"href"		:	ExecutionContext._URI,
				"src"		:	ExecutionContext._URI,
				"profile"	:	ExecutionContext._URI,
				"vocab"	    :   ExecutionContext._URI,
			
				"about"		:	ExecutionContext._CURIEorURI, 
				"resource"	:	ExecutionContext._CURIEorURI, 
			
				"rel"		:	ExecutionContext._TERMorCURIEorAbsURI,
				"rev"		:	ExecutionContext._TERMorCURIEorAbsURI,
				"datatype"	:	ExecutionContext._TERMorCURIEorAbsURI,
				"typeof"	:	ExecutionContext._TERMorCURIEorAbsURI,
				"property"	:	ExecutionContext._TERMorCURIEorAbsURI,
			}	
		#-----------------------------------------------------------------
		self.node = node
		
		#-----------------------------------------------------------------
		# Settling the base
		# It is done because it is prepared for a possible future change in direction of
		# accepting xml:base on each element.
		# At the moment, it is invoked with a 'None' at the top level of parsing, that is
		# when the <base> element is looked for (for the HTML cases, that is)
		if inherited_state :
			self.rdfa_version	= inherited_state.rdfa_version
			self.base			= inherited_state.base
			self.options		= inherited_state.options
			# for generic XML versions the xml:base attribute should be handled
			if self.options.host_language == HostLanguage.rdfa_core and node.hasAttribute("xml:base") :
				self.base = node.getAttribute("xml:base")
		else :
			# this is the branch called from the very top
			
			# get the version
			# Check if the 1.0 version is set explicitly
			html = node.ownerDocument.documentElement
			
			
			if html.hasAttribute("version") and RDFa_1_0_VERSION == html.getAttribute("version"):
				# The version has been set to be explicitly 1.0
				
				# It is still not 100% sure whether the version information is to control what happens here.
				# the commented code takes into account the rdfa 1.0 version attribute to possibly control
				# the version (switching off some rdfa1.1. specific features); the current just sets
				# the version to the version set in the parser as a global variable
				
				# @@@ This line should be restored if version must be taken into account
				# self.rdfa_version = "1.0"
				
				# @@@ The following 2 lines should be removed if the version must be taken into account
				from pyRdfa import rdfa_current_version
				self.rdfa_version = rdfa_current_version
			else :
				from pyRdfa import rdfa_current_version
				self.rdfa_version = rdfa_current_version

			# this is just to play safe. I believe this should actually not happen...
			if options == None :
				from pyRdfa import Options
				self.options = Options()
			else :
				self.options = options

			self.base = ""
			# handle the base element case for HTML
			if self.options.host_language in [ HostLanguage.xhtml_rdfa, HostLanguage.html_rdfa ] :
				for bases in node.getElementsByTagName("base") :
					if bases.hasAttribute("href") :
						self.base = bases.getAttribute("href")
						continue

			# xml:base is not part of XHTML+RDFa, but it is a valid in core
			if self.options.host_language == HostLanguage.rdfa_core and node.hasAttribute("xml:base") :
				self.base = node.getAttribute("xml:base")		

			# If no local setting for base occurs, the input argument has it
			if self.base == "" :
				self.base = base	
								
		#-----------------------------------------------------------------
		# this will be used repeatedly, better store it once and for all...
		self.parsedBase = urlparse.urlsplit(self.base)

		#-----------------------------------------------------------------
		# generate and store the local CURIE handling class instance
		self.curie = Curie(self, graph, inherited_state)

		#-----------------------------------------------------------------
		# Settling the language tags
		# @lang has priority over @xml:lang
		# it is a bit messy: the three fundamental modes (xhtml, html, or xml) are all slightly different:-(
		# first get the inherited state's language, if any
		if inherited_state :
			self.lang = inherited_state.lang
		else :
			self.lang = None
			
		if self.options.host_language in [ HostLanguage.xhtml_rdfa, HostLanguage.html_rdfa ] :
			# we may have lang and xml:lang
			if node.hasAttribute("lang") :
				lang = node.getAttribute("lang").lower()
			else :
				lang = None

			if node.hasAttribute("xml:lang") :
				xmllang = node.getAttribute("xml:lang").lower()
			else :
				xmllang = None
				
			# First of all, set the value, if any
			if xmllang != None :
				# this has priority
				if len(xmllang) != 0 :
					self.lang = xmllang
			elif lang != None :
				if len(lang) != 0 :
					self.lang = lang
				
			# check a posible warning (error?), too
			if lang != None and xmllang != None and lang != xmllang :
				self.options.add_warning("Both xml:lang and lang used on an element with different values; xml:lang prevails. (%s and %s)" % (xmllang, lang))			
		
		else :
			# this is a clear case, xml:lang is the only possible option...
			if node.hasAttribute("xml:lang") :
				self.lang = node.getAttribute("xml:lang").lower()
				if len(self.lang) == 0 : self.lang = None
			
		#-----------------------------------------------------------------
		# Set the default namespace. Used when generating XML Literals
		if node.hasAttribute("xmlns") :
			self.defaultNS = node.getAttribute("xmlns")
		elif inherited_state and inherited_state.defaultNS != None :
			self.defaultNS = inherited_state.defaultNS
		else :
			self.defaultNS = None

	#------------------------------------------------------------------------------------------------------------


	def _URI(self, val) :
		"""Returns a URI for a 'pure' URI (ie, no CURIE). The method resolves possible relative URI-s. It also
		checks whether the URI uses an unusual URI scheme (and issues a warning); this may be the result of an
		uninterpreted CURIE...
		@param val: attribute value to be interpreted
		@type val: string
		@return: an RDFLib URIRef instance
		"""

		def check_create_URIRef(uri) :
			"""
			Mini helping function: it checks whether a uri is using a usual scheme before a URIRef is created. In case
			there is something unusual, a warning is generated (though the URIRef is created nevertheless)
			@param uri: (absolute) URI string
			@return: an RDFLib URIRef instance
			"""
			val = uri.strip()
			if urlparse.urlsplit(val)[0] not in usual_schemes :
				self.options.add_warning("Unusual URI scheme used <%s>; may that be a mistake?" % val.strip())
			return URIRef(val)

		if val == "" :
			return URIRef(self.base)

		# fall back on good old traditional URI-s.
		# To be on the safe side, let us use the Python libraries
		if self.parsedBase[0] == "" :
			# base is, in fact, a local file name
			# The following call is just to be sure that some pathological cases when
			# the ':' _does_ appear in the URI but not in a scheme position is taken
			# care of properly...
			key = urlparse.urlsplit(val)[0]
			if key == "" :
				# relative URI, to be combined with local file name:
				return URIRef(self.base + val.strip())
			else :
				return check_create_URIRef(val)
		else :
			# Trust the python library...
			# Well, not quite:-) there is what is, in my view, a bug in the urljoin; in some cases it
			# swallows the '#' or '?' character at the end. This is clearly a problem with
			# Semantic Web URI-s
			joined = urlparse.urljoin(self.base, val)
			try :
				if val[-1] != joined[-1] :
					return check_create_URIRef(joined + val[-1])
				else :
					return check_create_URIRef(joined)
			except :
				return check_create_URIRef(joined)

	def _CURIEorURI(self, val) :
		"""Returns a URI for a (safe or not safe) CURIE. In case it is a safe CURIE but the CURIE itself
		is not defined, an error message is issued. 
		@param val: attribute value to be interpreted
		@type val: string
		@return: an RDFLib URIRef instance or None
		"""
		if val == "" :
			return URIRef(self.base)

		safe_curie = False
		if val[0] == '[' :
			# safe curies became almost optional, mainly for backward compatibility reasons
			# Note however, that if a safe curie is asked for, a pure URI is not acceptable.
			# Is checked below, and that is why the safe_curie flag is necessary
			if val[-1] != ']' :
				# that is certainly forbidden: an incomplete safe curie
				self.options.add_warning("Illegal CURIE: %s" % val, UnresolvablePrefix)
				return None
			else :
				val = val[1:-1]
				safe_curie = True
				
		# There is a branch here depending on whether we are in 1.1 or 1.0 mode
		if self.rdfa_version >= "1.1" :
			retval = self.curie.CURIE_to_URI(val)
			if retval == None :
				# the value could not be interpreted as a CURIE, ie, it did not produce any valid URI.
				# The rule says that then the whole value should be considered as a URI
				# except if it was part of a safe Curie. In that case it should be ignored...
				if safe_curie :
					self.options.add_warning("Safe CURIE was used but value does not correspond to a defined CURIE: [%s]" % val, UnresolvablePrefix)
					return None
				else :
					return self._URI(val)
			else :
				# there is an unlikely case where the retval is actually a URIRef with a relative URI. Better filter that one out
				if isinstance(retval, BNode) == False and urlparse.urlsplit(str(retval))[0] == "" :
					# yep, there is something wrong, a new URIRef has to be created:
					return URIRef(self.base+str(retval))
				else :
					return retval
		else :
			# in 1.0 mode a CURIE can be considered only in case of a safe CURIE
			if safe_curie :
				return self.curie.CURIE_to_URI(val)
			else :
				return self._URI(val)

	def _TERMorCURIEorAbsURI(self, val) :
		"""Returns a URI either for a term or for a CURIE. The value must be an NCNAME to be handled as a term; otherwise
		the method falls back on a CURIE or an absolute URI.
		@param val: attribute value to be interpreted
		@type val: string
		@return: an RDFLib URIRef instance or None
		"""
		# This case excludes the pure base, ie, the empty value
		if val == "" :
			return None
		from Curie import ncname
		if ncname.match(val) :
			# This is a term, must be handled as such...
			retval = self.curie.term_to_URI(val)
			if not retval :
				self.options.add_warning("Unresolvable term: %s" % val, UnresolvableTerm)
			else :
				return retval
		else :
			# try a CURIE
			retval = self.curie.CURIE_to_URI(val)
			if retval :
				return retval
			elif self.rdfa_version >= "1.1" :
				# See if it is an absolute URI
				scheme = urlparse.urlsplit(val)[0]
				if scheme == "" :
					# bug; there should be no relative URIs here
					self.options.add_warning("Relative URI is not allowed in this position: [%s]" % val, UnresolvablePrefix)
					return None
				else :
					if scheme not in usual_schemes :
						self.options.add_warning("Unusual URI scheme used <%s>; may that be a mistake?" % val.strip())
					return URIRef(val)
			else :
				print "here????"
				# rdfa 1.0 case
				self.options.add_warning("CURIE was used but value does not correspond to a defined CURIE: %s" % val.strip(), UnresolvablePrefix)
				return None

	# -----------------------------------------------------------------------------------------------

	def getURI(self, attr) :
		"""Get the URI(s) for the attribute. The name of the attribute determines whether the value should be
		a pure URI, a CURIE, etc, and whether the return is a single element of a list of those. This is done
		using the L{ExecutionContext._resource_type} table.
		@param attr: attribute name
		@type attr: string
		@return: an RDFLib URIRef instance (or None) or a list of those
		"""
		if self.node.hasAttribute(attr) :
			val = self.node.getAttribute(attr)
			val.strip()
		else :
			if attr in ExecutionContext._list :
				return []
			else :
				return None
		
		# This may raise an exception if the attr has no key. This, actually,
		# should not happen if the code is correct, but it does not harm having it here...
		try :
			func = ExecutionContext._resource_type[attr]
		except :
			# Actually, this should not happen...
			func = ExecutionContext._URI
		
		if attr in ExecutionContext._list :
			# Allows for a list
			resources = [ func(self, v.strip()) for v in val.split() if v != None ]
			retval = [ r for r in resources if r != None ]
		else :
			retval = func(self, val.strip())
		return retval
