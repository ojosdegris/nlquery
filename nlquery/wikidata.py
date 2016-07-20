from api_adapter import RestAdapter
from utils import dget
from dateutil import parser, relativedelta
from datetime import datetime
import arrow
from dateutil.relativedelta import relativedelta
from answer import Answer

class WikiDataAnswer(Answer):

    TIME_VALUE = 'http://wikiba.se/ontology#TimeValue'
    QUANTITY_VALUE = 'http://wikiba.se/ontology#QuantityValue'

    def __init__(self, sparql_query, bindings=None, data=None):
        super(WikiDataAnswer, self).__init__()
        self.sparql_query = sparql_query

        if bindings:
            self.bindings = bindings
            self.data = self.get_data(bindings)
        else:
            self.data = data

    def to_dict(self):
        d = super(WikiDataAnswer, self).to_dict()
        d['sparql_query'] = self.sparql_query
        return d

    @staticmethod
    def get_data(bindings):
        return [WikiDataAnswer.get_value(b) for b in bindings]

    @staticmethod
    def get_value(data):
        data_type = dget(data, 'type.value')
        value = dget(data, 'valLabel.value')

        if data_type == WikiDataAnswer.TIME_VALUE:
            dt = parser.parse(value)
            dt = dt.replace(tzinfo=None)
            return dt
        elif data_type == WikiDataAnswer.QUANTITY_VALUE:
            if value.isdigit():
                return float(value)
            return value
        else:
            return value

class WikiData(RestAdapter):
    WIKIDATA_URL = 'https://www.wikidata.org/w/api.php'
    WDSPARQL_URL = 'https://query.wikidata.org/sparql'

    def query_wdsparql(self, query):
        params = {
            'format': 'json',
            'query': query
        }
        self.debug(query)
        return self.get(self.WDSPARQL_URL, params=params)

    def query_wikidata(self, params):
        return self.get(self.WIKIDATA_URL, params=params)

    def search_entity(self, name, _type='item'):

        if _type not in ['item', 'property']:
            return None

        params = {
            'action': 'wbsearchentities',
            'format': 'json',
            'search': name,
            'language': 'en',
            'type': _type,
        }

        data = self.query_wikidata(params)
        return data

    def get_desc(self, subject):
        data = self.search_entity(subject)
        desc = dget(data, 'search.0.description')
        return Answer(data=desc)

    def get_id(self, name, _type='item'):
        item = self.search_entity(name, _type)
        return dget(item, 'search.0.id')

    def get_property(self, qtype, subject, prop):
        prop_id = None
        if prop is None:
            return self.get_desc(subject)
        if prop == 'age':
            bday_ans = self._get_property(subject, 'birthday')
            if not bday_ans:
                return None
            birthday = bday_ans.data[0]
            years = relativedelta(datetime.now(), birthday).years
            bday_ans.data = years
            return bday_ans

        if prop == 'born':
            if qtype == 'where':
                prop_id = 'P19'
            elif qtype == 'when':
                prop_id = 'P569'
        if prop == 'height':
            prop_id = 'P2044,P2048'

        if prop in ['nickname', 'known as', 'alias', 'called']:
            return self._get_aliases(subject)

        return self._get_property(subject, prop, prop_id=prop_id)

    def _get_property(self, subject, prop, prop_id=None):
        self.debug('{0}, {1}', subject, prop)
        subject_id = self.get_id(subject, 'item')

        if not prop_id:
            prop_id = self.get_id(prop, 'property')

        if not prop_id or not subject_id:
            return None

        query = """
        SELECT ?valLabel ?type
        WHERE {
        """
        sub_queries = []
        for pid in prop_id.split(','):
            sub_query = """{
                wd:%s p:%s ?prop . 
                ?prop ps:%s ?val .
                OPTIONAL {
                    ?prop psv:%s ?propVal .
                    ?propVal rdf:type ?type .
                }
            }""" % (subject_id, pid, pid, pid) 
            sub_queries.append(sub_query)
        query += ' UNION '.join(sub_queries)
        query += """
            SERVICE wikibase:label { bd:serviceParam wikibase:language "en"} 
        }
        """

        result =  self.query_wdsparql(query)
        bindings = dget(result, 'results.bindings')
        return WikiDataAnswer(sparql_query=query, bindings=bindings)


    def _get_aliases(self, subject):
        self.debug('Get alias {0}'.format(subject))
        subject_id = self.get_id(subject, 'item')
        query = """
        SELECT ?valLabel
        WHERE {
            { wd:%s skos:altLabel ?val FILTER (LANG (?val) = "en") }
            UNION
            { wd:%s rdfs:label ?val FILTER (LANG (?val) = "en") }
            SERVICE wikibase:label { bd:serviceParam wikibase:language "en"} 
        }""" % (subject_id, subject_id)

        result =  self.query_wdsparql(query)
        bindings = dget(result, 'results.bindings')
        return WikiDataAnswer(sparql_query=query, bindings=bindings)

    def find_entity(self, qtype, inst, props):
        if inst.lower() in ['the president', 'president', 'the prime minister', 'prime minister']:
            for index, tup in enumerate(props):
                prop, prop_val, op = tup
                if op == 'of':
                    inst = '{0} {1} {2}'.format(inst, op, prop_val)
                    props.pop(index)

        ans = self._find_entity(qtype, inst, props)

        return ans

    def _find_entity(self, qtype, inst, params):
        """
        Count number of things instance/subclass of inst with prop = prop_val
        :param qtype: Type of question (which, how many)
        :param inst: Instances of object we are querying
        :param params: Array of property-value-operator tuples to query [(property, value, op)]
                       property - property to match value
                       value - value that property should be
                       op - One of  '=', '<' or '>'
                       If property is None, then property will be inferred by instance of value
        :return: None if result not found
                 Number of results for query if qtype is how many
                 First 5 results if qtype is which
        """
        self.info('Get instances of {0} that are {1}'.format(inst, params))

        inst_id = self.get_id(inst)

        if not inst_id:
            self.info('Cannot find id of: {0}'.format(inst))
            return None

        if qtype == 'how many':
            select = '(count(*) as ?count)'
        elif qtype in ['which', 'who']:
            select = '?valLabel'
        else:
            self.warn('Qtype {0} not known'.format(qtype))
            return None

        query = """
                SELECT %s
                WHERE {
                { ?val p:P39 ?pos . # position held
                    ?pos ps:P39 wd:%s . # pos = inst
                    ?val wdt:P31 wd:Q5 . # as a human
                } UNION 
                {
                  ?val wdt:P31 wd:%s . # instance of 
                }
                """ % (select, inst_id, inst_id)

        for prop, prop_val, op in params:
            if op in ['>', '<']:
                prop_id = self.get_id(prop, 'property')
                self.info('Count number of {0} where {1} {2} {3}'.format(
                    inst, prop_id, op, prop_val))
                query += """
                        ?val wdt:%s ?value FILTER(?value %s %s) . # Filter by value
                        """ % (prop_id, op, prop_val)
            elif op in ['in', 'by', 'of']:
                if op == 'in' and prop_val.isdigit():
                    iso_time = parser.parse(prop_val).isoformat()

                    query += """
                    ?pos pq:P580 ?startDate . # pos.startDate
                    ?pos pq:P582 ?endDate . # pos.endDate
                    FILTER (?startDate < "%s"^^xsd:dateTime && ?endDate > "%s"^^xsd:dateTime)
                    """ % (iso_time, iso_time)
                elif op == 'of' and prop_val:
                    prop_val_id = self.get_id(prop_val)

                    query += """
                    ?pos pq:P108 wd:%s . # pos.employer
                    """ % (prop_val_id)
                else:
                    # Get value entity
                    prop_val_id = self.get_id(prop_val)
                    if prop:
                        prop_id = self.get_id(prop, 'property')
                        query += '?val wdt:%s wd:%s .\n' % (prop_id, prop_val_id)
                    else:
                        # Infer property of value
                        prop_id = '*'
                        query += """
                                 wd:%s wdt:P31 ?instance . # Get entities that value is an instance of. Ex: ?instance = wd:Q5107 (continent)
                                 ?instance wdt:P1687 ?propEntity . # instance of Entity to property. Ex: ?propEntity = wd:P30 (continent)
                                 ?propEntity wikibase:directClaim ?prop . # wd to wdt. Ex: ?prop = wdt:P30 (continent)
                                 ?val ?prop wd:%s .
                                 """ % (prop_val_id, prop_val_id)
                    self.info('Count number of {0} where {1}={2}'.format(
                        inst, prop_id, prop_val_id))

        query += 'SERVICE wikibase:label { bd:serviceParam wikibase:language "en"} }'

        result = {
            'sparql_query': query,
        }

        try:
            data = self.query_wdsparql(query)
        except ValueError:
            self.error('Error parsing data')
            return WikiDataAnswer(**result)

        if qtype == 'how many':
            result['data'] = dget(data, 'results.bindings.0.count.value')
        elif qtype in ['which', 'who']:
            result['bindings'] = dget(data, 'results.bindings')
            
        return WikiDataAnswer(**result)
