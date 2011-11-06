#!/usr/bin/env python

#
# Copyright 2011 Stef Walter
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2 of the licence or (at
# your option) any later version.
#
# See the included COPYING file for more information.
#

import os
import sys
import getopt
import unittest

import aes
import dh
import hkdf

import dbus
import dbus.service
import dbus.glib
import gobject

bus_name = 'org.freedesktop.Secret.MockService'

class NotSupported(dbus.exceptions.DBusException):
	def __init__(self, msg):
		dbus.exceptions.DBusException.__init__(self, msg, name="org.freedesktop.DBus.Error.NotSupported")

class InvalidArgs(dbus.exceptions.DBusException):
	def __init__(self, msg):
		dbus.exceptions.DBusException.__init__(self, msg, name="org.freedesktop.DBus.Error.InvalidArgs")

unique_identifier = 0
def next_identifier():
	global unique_identifier
	unique_identifier += 1
	return unique_identifier

class PlainAlgorithm():
	def negotiate(self, service, sender, param):
		if type (param) != dbus.String:
			raise InvalidArgs("invalid argument passed to OpenSession")
		session = SecretSession(service, sender, None)
		return (dbus.String("", variant_level=1), session)

class AesAlgorithm():
	def negotiate(self, service, sender, param):
		if type (param) != dbus.ByteArray:
			raise InvalidArgs("invalid argument passed to OpenSession")
		publi, privat = dh.generate_pair()
		peer = dh.bytes_to_number(param)
		ikm = dh.derive_key(privat, peer)
		key = hkdf.hkdf(ikm, 16)
		session = SecretSession(service, sender, key)
		return (dbus.ByteArray(dh.number_to_bytes(publi), variant_level=1), session)

class SecretSession(dbus.service.Object):
	def __init__(self, service, sender, key):
		self.sender = sender
		self.service = service
		self.key = key
		self.path = "/org/freedesktop/secrets/sessions/%d" % next_identifier()
		dbus.service.Object.__init__(self, service.bus_name, self.path)
		service.add_session(self)

	@dbus.service.method('org.freedesktop.Secret.Session')
	def Close(self):
		self.remove_from_connection()
		self.service.remove_session(self)


class SecretItem(dbus.service.Object):
	def __init__(self, collection, identifier, label="Item", attributes={ }):
		self.collection = collection
		self.identifier = identifier
		self.label = label
		self.attributes = attributes
		self.path = "/org/freedesktop/secrets/collection/%s/%s" % (collection.identifier, identifier)
		dbus.service.Object.__init__(self, collection.service.bus_name, self.path)
		collection.items[identifier] = self

	def match_attributes(self, attributes):
		for (key, value) in attributes.items():
			if not self.attributes.get(key) == value:
				return False
		return True


class SecretCollection(dbus.service.Object):
	def __init__(self, service, identifier, label="Collection", locked=False):
		self.service = service
		self.identifier = identifier
		self.label = label
		self.locked = locked
		self.items = { }
		self.path = "/org/freedesktop/secrets/collection/%s" % identifier
		dbus.service.Object.__init__(self, service.bus_name, self.path)
		service.collections[identifier] = self

	def search_items(self, attributes):
		results = []
		for item in self.items.values():
			if item.match_attributes(attributes):
				results.append(item)
		return results


class SecretService(dbus.service.Object):

	algorithms = {
		'plain': PlainAlgorithm(),
		"dh-ietf1024-sha256-aes128-cbc-pkcs7": AesAlgorithm(),
	}

	def __init__(self, name=None):
		if name == None:
			name = bus_name
		bus = dbus.SessionBus()
		self.bus_name = dbus.service.BusName(name, allow_replacement=True, replace_existing=True)
		dbus.service.Object.__init__(self, self.bus_name, '/org/freedesktop/secrets')
		self.sessions = { }
		self.collections = { }

		def on_name_owner_changed(owned, old_owner, new_owner):
			if not new_owner:
				for session in list(self.sessions.get(old_owner, [])):
					session.Close()

		bus.add_signal_receiver(on_name_owner_changed,
		                        'NameOwnerChanged',
		                        'org.freedesktop.DBus')

	def listen(self):
		loop = gobject.MainLoop()
		loop.run()

	def add_session(self, session):
		if session.sender not in self.sessions:
			self.sessions[session.sender] = []
		self.sessions[session.sender].append(session)

	def remove_session(self, session):
		self.sessions[session.sender].remove(session)

	@dbus.service.method('org.freedesktop.Secret.Service', byte_arrays=True, sender_keyword='sender')
	def OpenSession(self, algorithm, param, sender=None):
		assert type(algorithm) == dbus.String

		if algorithm not in self.algorithms:
			raise NotSupported("algorithm %s is not supported" % algorithm)

		return self.algorithms[algorithm].negotiate(self, sender, param)

	@dbus.service.method('org.freedesktop.Secret.Service')
	def SearchItems(self, attributes):
		locked = [ ]
		unlocked = [ ]
		items = [ ]
		for collection in self.collections.values():
			items = collection.search_items(attributes)
			if collection.locked:
				locked.extend(items)
			else:
				unlocked.extend(items)
		return (dbus.Array(unlocked, "o"), dbus.Array(locked, "o"))


def parse_options(args):
	global bus_name
	try:
		opts, args = getopt.getopt(args, "name", ["name="])
	except getopt.GetoptError, err:
		print str(err)
		sys.exit(2)
	for o, a in opts:
		if o in ("--name"):
			bus_name = a
		else:
			assert False, "unhandled option"
	return args

parse_options(sys.argv[1:])