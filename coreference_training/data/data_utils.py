import io

import os

import numpy as np

import pickle

from gensim.models import word2vec

from gensim.models.keyedvectors import KeyedVectors

from gensim.parsing.preprocessing import STOPWORDS

from textblob import Word

from textblob.wordnet import Synset

import nltk

import spacy

import wn_pos_conversion as wnconvert
from pywsd.lesk import simple_lesk
from nltk.corpus import stopwords

#buckets for wordnet similarity and cosine similarity

#the buckets are created manually in a preprocessing step

# the last bucket is for unknown similarities 

cosine_bucket = [-1, 0, 0.05, 0.1, 0.15, 0.2, 0.3, 0.4, 0.9, 1.0, None]

wordnet_bucket = [0.08,0.1,0.12,0.14,0.16,0.18,0.2,0.26,0.34,0.5,1, None]

pos_tagset, noun_senses, noun_events = [], [], []

#load upenn tag set
with io.open('data/upenn_tagset.txt','r',encoding='utf-8') as f:

    pos_tagset = f.read().split()

# load noun senses list
with open("eventive-noun-senses-ontonotes.txt",'r',encoding="utf8") as f:
    lines = f.readlines()[1:]
for l in lines:
    li = l.split(" ")
    noun_senses.append([li[0], int(li[1])]) 

# load wordnet nouns with eventive connotations
with open("wordnet_event_nouns.txt",'r',encoding="utf8") as f:
    lines = f.readlines()[8:]
for l in lines:
    noun_events.append(l.replace("\n",""))



class TextLoader():

    

    def __init__(self,sentences = None ,window = None,model_path = 'data\enwiki_stopwords.model',tensor_path=None, demo=False):

        # this is for testing purposes

        if sentences == None:

            self.load_model(model_path)

            return

        

        self.window = window

        self.nlp = spacy.load('en')

        self.demo = demo

        #if tensor_path is not None and os.path.exists(tensor_path):

            #print("Loading saved tensors...")

            #self.load_tensors(tensor_path)

        #else:

        self.load_model(model_path)

        print("Generating tensors...")

        if demo:

            self.features,self.labels,self.raw_data = self.gen_features_and_labels(sentences)                

        else:

            self.features,self.labels,self.raw_data = {},{},{}

            self.features['coref'],self.labels['coref'],self.raw_data['coref'] = self.gen_features_and_labels(sentences['coref'])

            self.features['incoref'],self.labels['incoref'],self.raw_data['incoref'] = self.gen_features_and_labels(sentences['incoref'])

        #if tensor_path is not None:

            #self.save_tensors(tensor_path)

                

        self.features_size = ( 2 * (2*self.window+1) * self.model_shape +  # size of the window (Contextual features)

                               2 * len(pos_tagset) + 

                               #2 * 3 + #event information (Event Features)

                               2 +  # distance between sentences (Relational Features) and bit for doc-id equality in pair

                               1 * len(cosine_bucket) + # hotvector of mention headword similarity and avg mention similarity (Relational Features)

                               3 * len(wordnet_bucket) ) # hotvector of mention headword similarity and hypernyms (Relational Features)



    #Loading the word2vec model

    def load_model(self, model_path):

        print("Loading word2vec model...")

        word2vec_model = word2vec.Word2Vec.load(model_path)

        #word2vec_model = KeyedVectors.load_word2vec_format(model_path, binary=True)

        self.model = word2vec_model

        self.model_shape = self.model['news'].shape[0]      



    def save_tensors(self, tensors_path):

        tensors = [self.features, self.labels,self.model_shape,self.raw_data]

        with io.open(tensors_path, 'wb') as f:

            pickle.dump(tensors, f)





    def load_tensors(self, tensors_path):

        with io.open(tensors_path, 'rb') as f:

            tensors = pickle.load(f)

        self.features = tensors[0]

        self.labels = tensors[1]

        self.model_shape = tensors[2]

        self.raw_data = tensors[3]



    # input: list of words and a window(k)

    # returns the first k words that occurs in the model

    # returns a row of size k*model_shape

    def k_words_embeddings(self,words_list,k):

        row = []

        i = 0

        for e in words_list:

            if i >= k:

                break

            elif e in self.model:

                row.extend(self.model[e.lower()].tolist())

                i = i + 1

        while i < k:

            row.extend([0]*self.model_shape) #out of bounds

            i = i + 1

        return row





    def wordlist_average(self,wordlist):

        total = np.zeros((self.model_shape,),dtype="float32")

        nwords = 0

        for word_i in wordlist:

            word = word_i.lower()

            if word in self.model:

                total = np.add(total, self.model[word])

                nwords = nwords + 1

        if nwords < 1:

            return None

        else:

            return np.divide(total,nwords).tolist()







    def get_word2vec_sim(self,a1,a2):

        if a1.lower() in self.model and a2.lower() in self.model:

            return self.model.similarity(a1.lower(),a2.lower())

        else:

            return None

            

    #Intended for similarity of the mentions average

    def cosine_similarity(self,a,b):

        return np.dot(a, b)/(np.linalg.norm(a)*np.linalg.norm(b))

    

    #wordnet path similarity is not an equivalence relation

    def wordnet_path_sim(self,s1,s2):

        sim1 = s1.path_similarity(s2)

        sim2 = s2.path_similarity(s1)

        

        if sim1 == None or (sim2 != None and sim2 > sim1):

            return sim2

        else:

            return sim1

        

    def get_max_synsets(self,synsets1,synsets2):

        sim_max = None

        max_syn1 = None

        max_syn2 = None

        for synset1 in synsets1:

            for synset2 in synsets2:

                sim = self.wordnet_path_sim(synset1,synset2) 

                if sim_max == None or (sim != None and sim > sim_max):

                    sim_max = sim

                    max_syn1 = synset1

                    max_syn2 = synset2

                    

        return sim_max,max_syn1,max_syn2



    def convert_to_verb(self,max_syn,mention):

        if max_syn is not None and max_syn.pos() != 'v':

            a1 = []

            for m in mention:

                x = wnconvert.convert(m,'n','v')

                if len(x) < 1:

                    a1.append(m)

                else:

                    a1.append(x[0][0])

            return a1

        else:

            return mention

    

    def get_wordnet_sim(self,m1,m2,all_sim = True):

        a1,a2 = [],[]

        for m in m1:

            a1.append(Word(m).lemmatize("v"))

        for m in m2:

            a2.append(Word(m).lemmatize("v"))

        

        syn1 = Word("_".join(a1)).synsets

        syn2 = Word("_".join(a2)).synsets



        if len(syn1) < 1:

           for m in m1:

               syn1.extend(Word(m).synsets)

               

        if len(syn2) < 1:

           for m in m2:

               syn2.extend(Word(m).synsets)

    

        sim_max , max_syn1 , max_syn2 = self.get_max_synsets(syn1,syn2)

        hyper_sim = None

        verb_convert_sim = None

        if all_sim:

            if sim_max != None:

                hyp1,_ = zip(*max_syn1.hypernym_distances())

                hyp2,_ = zip(*max_syn2.hypernym_distances())

                hyper_sim, _, _ = self.get_max_synsets(list(hyp1),list(hyp2))

            # new similarity measure by converting everything to a verb

            a1,a2 = self.convert_to_verb(max_syn1,m1),self.convert_to_verb(max_syn2,m2)

            verb_convert_sim,_,_ = self.get_wordnet_sim(a1,a2,False)

                

        return sim_max,hyper_sim, verb_convert_sim

    

    def get_bucket(self,t, x):

                counter = [0] *len(t)

                if x == None:

                    counter[len(t)-1] = 1

                    return counter

                i = 0

                while i < len(t) - 2 and t[i] < float(x):

                    i = i + 1

                counter[i] = counter[i] + 1

                return counter

            

    def get_mention(self,sent_dict):

        s = sent_dict['start_index']

        e = sent_dict['end_index']

        return sent_dict['sentence'][s:e+1]

    

    # generates the embeddings of the the mention and the window arounds it

    # generates the head word

    # return the embeddings of the window.

    # If none of the words of the mention appear in the model, it returns none (can't be computed)

    def contextual_features(self,sent_dict):

        sentence = sent_dict['sentence']

        s = sent_dict['start_index']

        e = sent_dict['end_index']

        sentence = [x.replace('\n','')for x in sentence]

        row = []

        before_mention = sentence[:s]

        after_mention = sentence[e+1:]

        s = sent_dict['start_index']

        e = sent_dict['end_index']

        mention = sentence[s:e+1]

        #self.get_mention(sent_dict)

        
        # print('inside contextual features - before doc')
        #getting the headword

        doc = self.nlp(" ".join(mention))
        # print(" mentions joined:> ", " ".join(mention))
        # print("doc:> ", doc)

        sents = list(doc.sents)
        # print("sents list:> ", sents)
        try:

            head_word = str(sents[0].root).lower()

            head_word = (Word(head_word))



        except:
            print('inside except statement')
            print('sent dict:> ',sent_dict)
            print('mention:> ', mention)

            print ('Can\'t be encoded into string', sents[0].root)

            head_word = sents[0].root

            head_word = (Word(head_word))

        #if unicode(sents[0].root) not in mention:

           #print 'Inconsistent tokenization for headword', sents[0].root,' in mention', mention, 'in sentence', sentence   

        

         #.stem()

        

        row.extend(self.k_words_embeddings(reversed(before_mention),self.window))

        m_avg = self.wordlist_average(mention)

        

        if not self.demo and m_avg == None:

            return None,None,None

        else:

            #Adding the mention's average embeddings

            row.extend(m_avg)

           

            #Adding the headword embedding

            #row.extend(self.words_vec([head_word],1))

            row.extend(self.k_words_embeddings(after_mention,self.window))



            postag_row = [0] * len(pos_tagset)

            m_pt = nltk.pos_tag(mention)

            for w,tag in m_pt:

                pi = pos_tagset.index(tag)

                postag_row[pi] = postag_row[pi] + 1

                

            row.extend(postag_row)

            return row, head_word, m_avg



    # creates one row of input for the neural network

    # return None, if the none of the words in the mention are in the word2vec model

    def get_row(self,sentence_i, sentence_j):

        data_row = []            

        # appending the vecotrs for the chosen window for both sentences

        data_row_i, mention_i, avg_i = self.contextual_features(sentence_i)

        data_row_j ,mention_j, avg_j = self.contextual_features(sentence_j)

        if avg_i == None or avg_j == None:

            return None

        

        else:   

            # appending the vecotrs for the chosen window for sentences_i

            data_row.extend(data_row_i)

                             

            #repeat for j

            data_row.extend(data_row_j)

            

            # adds any additional features

            #       feature 1: distance between senteces 

            data_row.append(sentence_j['sentence_index'] - sentence_i['sentence_index'])



            data_row.append(int(sentence_j['doc_id']==sentence_i['doc_id']))

            

            #       feature 2: cosine sim of headwords of mentions

            cosine_sim = self.get_word2vec_sim(mention_i,mention_j)

            data_row.extend(self.get_bucket(cosine_bucket, cosine_sim))

            

            #       feature 3: cosine sim of average mentions

            #avg_cosine_sim = self.cosine_similarity(avg_i,avg_j)

            #data_row.extend(self.get_bucket(cosine_bucket, avg_cosine_sim))

            #       feature 4 & 5 : wordnet sim of mentions and their hypernyms

            wordnet_sim,wordnet_hypersim,wordnet_verbsim = self.get_wordnet_sim(\

                self.get_mention(sentence_i),\

                self.get_mention(sentence_j))

            data_row.extend(self.get_bucket(wordnet_bucket, wordnet_sim))

            data_row.extend(self.get_bucket(wordnet_bucket, wordnet_hypersim))

            data_row.extend(self.get_bucket(wordnet_bucket, wordnet_verbsim))

            

            return data_row

        

    # The following is used to generate the 'raw_data'

    def get_raw_data(self,link,keys,values):

        mention = link.copy()

        for k,v in zip(keys,values):

            mention[k] = v

        mention['raw_data'] = self.get_mention_line(mention)

        return mention

        

    def get_mention_line(self,mention):

        data = ["is_coref","doc_id","chain_index","link_index", \

                "sentence_index","pos_tag","start_index","end_index"]

        mention_s = ""

        for key in data:

            mention_s = mention_s + str(mention[key]) + " "

        mention_s = mention_s + " ".join(mention["sentence"])

        return mention_s.rstrip()

    #returns pair of indices in link 1 and 2 with highest sim
    def pick_most_likely_event(self, link1, guess_l1, link2, guess_l2):
        sent1 = link1['sentence']
        sent2 = link2['sentence']
        #for every pair of guesses
        maxsim = None
        maxpair = [-1,-1]
        for i1 in guess_l1:
            for i2 in guess_l2:
                sim = self.get_word2vec_sim(sent1[i1],sent2[i2]) #sim btwn 2 guesses
                if (sim == None):
                    continue #ignore this pair
                else:
                    if (maxsim == None): #first time
                        maxsim = sim
                        maxpair = [i1,i2]
                    elif (maxsim < sim): #recorded max is beaten
                        maxsim = sim
                        maxpair = [i1,i2]
        if (maxpair[0]==-1 or maxpair[1] == -1):
            print('link 1:> ', link1)
            print('link 2:> ', link2)
            print('guess_l1:> ', guess_l1)
            print('guess_l2:> ', guess_l2)
        return maxpair[0],maxpair[1]

    def get_event_guesses(self,link):
        tokens = link['sentence']
        pos_tokens = nltk.pos_tag(tokens)
        guess, guess_backup, guess_all = [], [], []
        for i in range(len(tokens)):
            guess_all.append(i)
            word = tokens[i].lower()
            word_lem_n = Word(word).lemmatize('n')
            word_lem_v = Word(word).lemmatize('v')
            lesk_syn = simple_lesk((" ").join(tokens), word)
            if ((word == '.\n') or (word in stopwords.words('english'))):
                continue #EOL or stop word; ignore
            guess_backup.append(i)
            if ('VB' in pos_tokens[i][1] or \
                 word in noun_events or word_lem_n in noun_events or word_lem_v in noun_events): #verb then add it in
                guess.append(i)
            elif ('NN' in pos_tokens[i][1] and lesk_syn):
                lesk_list = lesk_syn.name().split('.')
                if ((len(lesk_list) == 3) and ([lesk_list[0],int(lesk_list[2])] in noun_senses)):
                    guess.append(i) #word disamb is in noun sense list matching sense
        # if (len(guess)==0):
        #     # print('no guesses made here')
        #     # print('link sentence:> ', tokens)
        #     # print('pos tags:> ', pos_tokens)
        #     # print('actual event:> ', tokens[link['start_index']:link['end_index']+1])
        #     # print('link:> ', link)
        #     # print()
        #     # print()
        #     return guess_backup
        return guess_all


    #here we modify the exisiting start and end indices
    def gen_features_and_labels(self,chains,data=""):
        print('inside generate features and labels....')

        x = []

        y = []

        raw_data = []

        row_id = 0

        for chain in chains:

            for pi in range(len(chain)):

                for pj in range(pi+1,len(chain)):

                    if row_id % 500 == 0:

                        print("Processing row ", row_id,"...")

                    #modify indices now
                    event_guess1 = self.get_event_guesses(chain[pi])
                    event_guess2 = self.get_event_guesses(chain[pj])
                    if (len(event_guess1) == 0 or len(event_guess2)==0):
                        print('still here - this is a problem!!!!')
                        i1,i2 = 0,0 #pick an index at random
                    else:
                        i1,i2 = self.pick_most_likely_event(chain[pi],event_guess1,chain[pj],event_guess2)
                    chain[pi]['start_index'] = i1
                    chain[pi]['end_index'] = i1
                    chain[pj]['start_index'] = i2
                    chain[pj]['end_index'] = i2

                    #continue as usual after modification ....

                    #i should preceed j

                    if ((chain[pj]['sentence_index'] > chain[pi]['sentence_index']) or \
                       ((chain[pj]['sentence_index'] == chain[pi]['sentence_index']) and (chain[pj]['start_index'] > chain[pi]['start_index']))):
                        j = pj
                        i = pi
                    else:
                        j = pi
                        i = pj
                    
                    data_row = self.get_row(chain[i],chain[j])

                    if data_row != None:

                        x.append(data_row)

                        if 'is_coref' in chain[i]:

                            y.append(chain[i]['is_coref'])

                            raw_data.append([self.get_raw_data(chain[i],['chain_index','link_index'],[row_id,0]),\

                                         self.get_raw_data(chain[j],['chain_index','link_index'],[row_id,1])])

                    row_id = row_id + 1

        

        return x,y, raw_data

# data format

# is_coref doc_id chain_index link_index sentence_index

#pos_tag start_word_index end_word_index sentence                                                                                                                                                           

#shoudl take lines as well caevo lines

def read_file(lines,caevo):

    chains = []

    this_chain = []

    prev_chain = None

    for idx in range(len(lines)):
        line=lines[i] #a line from input data

        try:
            
            tokens = line.split(" ")

            is_coref = int(tokens[0])

            doc_id = int(tokens[1])

            chain_index = int(tokens[2]) 

            link_index = int(tokens[3]) 

            sentence_index = int(tokens[4])

            pos_tag = tokens[5]

            start_index = int(tokens[6]) 

            end_index = int(tokens[7])

            sentence = tokens[8:]

            #now include caevo events for this sentence
            cv_events = caevo[idx-1]['events'['event']

            mention = {"raw_data":line,"pos_tag":pos_tag, \

                       "sentence_index": sentence_index, \

                       "start_index":start_index, \

                       "end_index":end_index, "sentence":sentence, \

                       "is_coref":is_coref,"doc_id":doc_id,\

                       "caevo_event":cv_events, \

                       "ontonotes_event":-1,\

                       "wordnet_event":-1}

            

            if prev_chain == None:

                    prev_chain = chain_index

            if prev_chain == chain_index:

                this_chain.append(mention)

            else:

                chains.append(this_chain)

                this_chain = [mention]

                prev_chain = chain_index

                

        except:

            print("line_skipped")

    chains.append(this_chain)          

    return chains



def batch_iter(data, batch_size, num_epochs, shuffle=True):

    """

    Generates a batch iterator for a dataset.

    """

    data = np.array(data)

    data_size = len(data)

    num_batches_per_epoch = int((len(data)-1)/batch_size) + 1

    for epoch in range(num_epochs):

        # Shuffle the data at each epoch

        if shuffle:

            np.random.seed(0)

            shuffle_indices = np.random.permutation(np.arange(data_size))

            shuffled_data = data[shuffle_indices]

        else:

            shuffled_data = data

        for batch_num in range(num_batches_per_epoch):

            start_index = batch_num * batch_size

            end_index = min((batch_num + 1) * batch_size, data_size)

            yield shuffled_data[start_index:end_index]


# #RUN stuff here

# with open("data/ecb_coref_train.txt",'r', encoding="utf8") as f:
#     sentences = f.readlines()

# sentences = read_file(sentences)
# textloader = TextLoader(sentences,2,"data/enwiki_stopwords.model",demo=True)

