"""Offline address lookup must preserve house numbers and geographic qualifiers."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from engine.address_search import find_addresses, is_address_query, normalize


class AddressSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.geo = Path(self.temp.name)
        (self.geo / 'addresses').mkdir()
        self.write_state('MI', [
            ('18001', 'W Nine Mile Road', 'Novi', 'MI', '48375', 42.45, -83.47),
            ('18002', 'W Nine Mile Road', 'Novi', 'MI', '48375', 42.46, -83.47),
            ('18001', 'Nine Mile Rd', 'Farmington', 'MI', '48335', 42.47, -83.40),
        ])
        self.write_state('CA', [
            ('18001', 'Nine Mile Road', 'Example', 'CA', '90001', 34.0, -118.0),
            ('23001', 'Maple Avenue', 'Kansas City', 'MO', '64105', 39.0997, -94.5786),
            ('21', 'W 9 ST', 'Example', 'CA', '90001', 34.01, -118.01),
        ])

    def tearDown(self):
        self.temp.cleanup()

    def write_state(self, state, rows):
        db = sqlite3.connect(self.geo / 'addresses' / f'{state}.sqlite')
        try:
            db.execute('CREATE TABLE addr(num TEXT,street TEXT,norm TEXT,city TEXT,state TEXT,zip TEXT,lat REAL,lon REAL)')
            db.execute('CREATE INDEX addr_num_norm ON addr(num,norm)')
            db.executemany('INSERT INTO addr VALUES(?,?,?,?,?,?,?,?)',
                           [(num, street, normalize(street), city, st, z, lat, lon)
                            for num, street, city, st, z, lat, lon in rows])
            db.commit()
        finally:
            db.close()      # the `with` form commits but does not close, and Windows will not remove an open file

    def test_numeric_words_directions_and_qualifiers(self):
        for query in ('18001 9 mile rd Novi MI', '18001 Nine Mile Road Novi Michigan',
                      '18001 nine mile Novi 48375', '18001 W Nine Mile Road, Novi, MI 48375'):
            with self.subTest(query=query):
                result = find_addresses(query, self.geo)
                self.assertEqual(len(result), 1)
                self.assertEqual(result[0]['name'], '18001 W Nine Mile Road')
                self.assertEqual(result[0]['lat'], 42.45)
                self.assertEqual(result[0]['kind'], 'Street address')

    def test_unknown_number_does_not_return_nearby_house(self):
        self.assertEqual(find_addresses('18003 nine mile Novi MI', self.geo), [])

    def test_city_and_zip_do_not_silently_fall_back(self):
        self.assertEqual(find_addresses('18001 nine mile Wrongtown MI', self.geo), [])
        self.assertEqual(find_addresses('18001 nine mile MI 99999', self.geo), [])

    def test_city_names_use_the_same_normalization_as_the_query(self):
        self.write_state('SC', [
            ('100', 'Example Road', 'Mount Pleasant', 'SC', '29464', 0.0, 0.0),
            ('100', 'Example Road', 'North Charleston', 'SC', '29405', 0.0, 0.0),
            ('100', 'Example Road', None, 'SC', '29464', 0.0, 0.0),
        ])
        self.write_state('MO', [
            ('100', 'Example Road', 'St. Louis', 'MO', '63101', 0.0, 0.0),
            ('100', 'Example Road', 'Saint Charles', 'MO', '63301', 0.0, 0.0),
        ])
        for city, state, postcode, spellings in (
                ('Mount Pleasant', 'SC', '29464', ('Mount Pleasant', 'Mt Pleasant', 'Mt. Pleasant')),
                ('North Charleston', 'SC', '29405', ('North Charleston', 'N Charleston')),
                ('St. Louis', 'MO', '63101', ('Saint Louis', 'St Louis', 'St. Louis')),
                ('Saint Charles', 'MO', '63301', ('Saint Charles', 'St Charles'))):
            for spelling in spellings:
                with self.subTest(city=city, spelling=spelling):
                    result = find_addresses(f'100 Example Road {spelling} {state}', self.geo)
                    self.assertEqual(len(result), 1)
                    self.assertEqual(result[0]['detail'], f'{city}, {state}, {postcode}')
        self.assertEqual(find_addresses('100 Example Rd Wrongtown SC', self.geo), [])
        self.assertEqual(find_addresses('100 Example Rd Mt Pleasant SC 29405', self.geo), [])
        self.assertEqual(find_addresses('101 Example Rd Mt Pleasant SC', self.geo), [])

    def test_state_uses_only_that_index(self):
        result = find_addresses('18001 nine mile CA', self.geo)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['detail'], 'Example, CA, 90001')

    def test_ordinal_street(self):
        result = find_addresses('21 ninth st Example CA', self.geo)
        self.assertEqual(result[0]['name'], '21 W 9 ST')

    def test_plain_zip_remains_place_query(self):
        self.assertFalse(is_address_query('48375'))
        self.assertFalse(is_address_query('Novi MI'))
        self.assertTrue(is_address_query('18001 nine mile'))

    def test_missing_indexes_and_legacy_database(self):
        empty = self.geo / 'empty'
        self.assertEqual(find_addresses('23001 Maple Ave', empty), [])
        empty.mkdir()
        db = sqlite3.connect(empty / 'addresses.sqlite')
        try:
            db.execute('CREATE TABLE addr(num TEXT,street TEXT,norm TEXT,city TEXT,state TEXT,zip TEXT,lat REAL,lon REAL)')
            db.execute("INSERT INTO addr VALUES('23001','Maple Avenue','maple ave','Kansas City','MO','64105',39.0997,-94.5786)")
            db.execute("INSERT INTO addr VALUES('100','Example Road','example rd','St. Louis','MO','63101',0,0)")
            db.commit()
        finally:
            db.close()      # as in write_state: the `with` form does not close, and Windows will not remove an open file
        self.assertEqual(find_addresses('23001 Maple Ave Kansas City MO', empty)[0]['lat'], 39.0997)
        self.assertEqual(find_addresses('100 Example Rd Saint Louis MO', empty)[0]['detail'], 'St. Louis, MO, 63101')

    def test_app_route_preserves_address_number(self):
        import server
        with patch.object(server, 'HERE', self.geo.parent), patch.object(server.address_search, 'find_addresses', return_value=[]) as lookup:
            self.assertEqual(server.places('18003 nine mile'), [])
            lookup.assert_called_once_with('18003 nine mile', self.geo.parent / 'derived' / 'geo', limit=8, near=None)


if __name__ == '__main__':
    unittest.main()
