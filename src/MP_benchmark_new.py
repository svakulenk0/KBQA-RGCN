#!/usr/bin/env python
# -*- coding: utf-8 -*-

'''
Created on Dec 9, 2018

.. codeauthor: svitlana vakulenko
    <svitlana.vakulenko@gmail.com>

Message Passing for KBQA
'''
# setup
dataset_name = 'lcquad'

import os
os.chdir('/home/zola/Projects/temp/KBQA/util')
from setup import Mongo_Connector
mongo = Mongo_Connector('kbqa', dataset_name)

# path to KG relations
from hdt import HDTDocument
hdt_path = "/home/zola/Projects/hdt-cpp-molecules/libhdt/data/"
hdt_file = 'dbpedia2016-04en.hdt'
namespace = "http://dbpedia.org/"


import numpy as np
import scipy.sparse as sp


def generate_adj_sp(adjacencies, adj_shape, normalize=False, include_inverse=False):
    '''
    Build adjacency matrix
    '''
    # colect all predicate matrices separately into a list
    sp_adjacencies = []
    for edges in adjacencies:
        # split subject (row) and object (col) node URIs
        n_edges = len(edges)
        row, col = np.transpose(edges)
        
        # duplicate edges in the opposite direction
        if include_inverse:
            _row = np.hstack([row, col])
            col = np.hstack([col, row])
            row = _row
            n_edges *= 2
        
        # create adjacency matrix for this predicate TODO initialise matrix with predicate scores
        data = np.ones(n_edges, dtype=np.int8)
        adj = sp.csr_matrix((data, (row, col)), shape=adj_shape, dtype=np.int8)
        
        if normalize:
            adj = normalize_adjacency_matrix(adj)

        sp_adjacencies.append(adj)
    
    return np.asarray(sp_adjacencies)

max_triples = 500000
from collections import defaultdict

def hop(activations, constraints, predicates_ids, verbose=False, _bool_answer=False):
    # extract the subgraph for the selected entities
    top_entities_ids = activations + constraints
    
    # get all the related predicates
    # kg = HDTDocument(hdt_path+hdt_file)
    # kg.configure_hops(1, [], namespace, True)
    # _, predicate_ids, _ = kg.compute_hops(top_entities_ids, 100000, 0)
    # kg.remove()

    # exclude types predicate
    top_predicates_ids = [_id for _id in predicates_ids if _id != 68655]
    
    # iteratively call the HDT API to retrieve all subgraph partitions
    activations = defaultdict(int)
    offset = 0
    while True:
        # get the subgraph for selected predicates only
        kg = HDTDocument(hdt_path+hdt_file)
        kg.configure_hops(1, top_predicates_ids, namespace, True)
        entities, predicate_ids, adjacencies = kg.compute_hops(top_entities_ids, max_triples, offset)
        kg.remove()
    
        if not entities:
            # filter out the answers by min activation scores
            if not _bool_answer and constraints:
                # normalize activations by checking the 'must' constraints: number of constraints * weights
                min_a = len(constraints) * 1
                if predicates_ids != top_predicates_ids:
                    min_a -= 1
            else:
                min_a = 0
            # return HDT ids of the activated entities
            return [a_id for a_id, a_score in activations.items() if a_score > min_a]

        if verbose:
            print("Subgraph extracted:")
            print("%d entities"%len(entities))
            print("%d predicates"%len(predicate_ids))
            print("Loading adjacencies..")

        offset += max_triples
        # index entity ids global -> local
        entities_dict = {k: v for v, k in enumerate(entities)}
        adj_shape = (len(entities), len(entities))
        # generate a list of adjacency matrices per predicate assuming the graph is undirected wo self-loops
        A = generate_adj_sp(adjacencies, adj_shape, include_inverse=True)

        # activations of entities and predicates
        e_ids = [entities_dict[entity_id] for entity_id in top_entities_ids if entity_id in entities_dict]
    #     assert len(top_entities_ids) == len(e_ids)
        p_ids = [predicate_ids.index(entity_id) for entity_id in top_predicates_ids if entity_id in predicate_ids]
    #     assert len(top_predicates_ids) == len(p_ids)
        if p_ids:
            # graph activation vectors
            x = np.zeros(len(entities))
            x[e_ids] = 1
            p = np.zeros(len(predicate_ids))
            p[p_ids] = 1

            # slice A by the selected predicates and concatenate edge lists
            y = (x@sp.hstack(A*p)).reshape([len(predicate_ids), len(entities)]).sum(0)
            # check output size
            assert y.shape[0] == len(entities)
            
            # harvest activations
            top = np.argwhere(y > 0).T.tolist()[0]
            if len(top) > 0:
                activations1 = np.asarray(entities)[top]
                # store the activation values per id answer id
                for i, e in enumerate(entities):
                    if e in activations1:
                        activations[e] += y[i]


limit = None
cursor = mongo.get_sample(limit=limit)

#samples = [mongo.get_by_id("392")]  # look up sample by serial number
verbose = True

# hold average stats for the model performance over the samples
ps, rs, fs = [], [], []

with cursor:
    while True:
        doc = next(cursor, None)
        if not doc:
            break
        print(doc['SerialNumber'])

        if verbose:
            print(doc['question'])
            print(doc['sparql_query'])
            print(doc["1hop"])
            print(doc["2hop"])

        assert doc['train'] == True

         # check question type
        _bool_answer = doc['question_type'] == 'ASK'

        top_entities_ids1 = doc['1hop_ids'][0]
        top_predicates_ids1 = doc['1hop_ids'][1]
        answers_ids = hop([top_entities_ids1[0]], top_entities_ids1[1:], top_predicates_ids1, verbose, _bool_answer)

        _2hops = doc['2hop'] != [[], []]
        if _2hops:
            top_entities_ids2 = doc['2hop_ids'][0]
            top_predicates_ids2 = doc['2hop_ids'][1]
            answers_ids = hop(answers_ids, top_entities_ids2, top_predicates_ids2, verbose)

        # error estimation
        if _bool_answer:
            answer = all(x in answers_ids for x in doc["entity_ids"])
            gs_answer = doc['bool_answer']
            if answer == gs_answer:
                p, r, f = 1, 1, 1
            else:
                p, r, f = 0, 0, 0
        else:
            answers_ids = set(answers_ids)
            n_answers = len(answers_ids)
            gs_answer_ids = set(doc['answers_ids'])
            n_gs_answers = len(gs_answer_ids)
            n_correct = len(answers_ids & gs_answer_ids)

            if verbose:
                print("%d predicted answers:"%n_answers)
    #             print(set(answers_uris)[:5])
                print("%d gs answers:"%n_gs_answers)
    #             print(set(doc['answers']))
                print(n_correct)

            try:
                r = float(n_correct) / n_gs_answers
            except ZeroDivisionError:\
                print(doc['question'])
            try:
                p = float(n_correct) / n_answers
            except ZeroDivisionError:
                p = 0
            try:
                f = 2 * p * r / (p + r)
            except ZeroDivisionError:
                f = 0
        print("P: %.2f R: %.2f F: %.2f"%(p, r, f))

        # add stats
        ps.append(p)
        rs.append(r)
        fs.append(f)


print("\nFin. Results for %d questions:"%len(ps))
print("P: %.2f R: %.2f F: %.2f"%(np.mean(ps), np.mean(rs), np.mean(fs)))
