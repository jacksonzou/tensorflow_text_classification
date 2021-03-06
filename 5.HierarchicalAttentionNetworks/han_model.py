#!/usr/local/miniconda2/bin/python
# _*_ coding: utf-8 _*_

"""
@author: MarkLiu
@time  : 17-12-13 下午9:56
"""
from __future__ import absolute_import, division, print_function

import os
import sys

module_path = os.path.abspath(os.path.join('..'))
sys.path.append(module_path)

import tensorflow as tf
import tensorflow.contrib.layers as layers


class HierarchicalAttentionNetworks(object):
    def __init__(self, label_size, num_sentences, sequence_length, vocabulary_size,
                 embedding_dim, word_encoder_bigru_num_units,
                 word_attention_size, sent_attention_size,
                 l2_reg_lambda=1e-4, activation=tf.nn.relu,
                 embedding_trainable=False
                 ):
        """
        :param label_size: 
        :param num_sentences: the numbers of splited sentences about sequence
        :param sequence_length: the length of input total sequence
        :param vocabulary_size: 
        :param embedding_dim: 
        :param embedding_trainable: 
        :param word_encoder_bigru_num_units: bigru num_units
        """
        self.label_size = label_size
        self.num_sentences = num_sentences
        self.sequence_length = sequence_length
        self.vocabulary_size = vocabulary_size
        self.embedding_dim = embedding_dim
        self.embedding_trainable = embedding_trainable
        self.l2_reg_lambda = l2_reg_lambda
        self.activation = activation

        self.word_attention_size = word_attention_size
        self.sent_attention_size = sent_attention_size

        # the length of every sentences in document
        self.sentence_length = int(self.sequence_length / self.num_sentences)
        self.word_encoder_bigru_num_units = word_encoder_bigru_num_units

        # Placeholders for input, output
        # document represented as: [num_sentences, sentence_length]
        self.inputs = tf.placeholder(tf.int32, shape=[None, self.sequence_length], name='input_x')
        self.labels = tf.placeholder(tf.int8, shape=[None, self.label_size], name='input_y')
        self.dropout_keep_prob = tf.placeholder(tf.float32, name='dropout_keep_prob')

        self.learning_rate = tf.placeholder(tf.float32, name='learning_rate')

        # build HAN model
        self.build_model()

    def build_model(self):
        """
        build HAN model architecture
        1. embeddding layer
        2. bi-GRU word encoder layer
        3. word attention layer --> sentences representation
        4. bi-GRU sentences encoder layer
        5. sentences attention layer --> document representation
        6. FC layer + softmax
        """
        # 1. Embedding layer
        with tf.name_scope('word_embedding'):
            self.embedding_matrix = tf.get_variable(shape=[self.vocabulary_size, self.embedding_dim],
                                                    initializer=layers.xavier_initializer(uniform=True),
                                                    name='embedding_matrix', dtype=tf.float32,
                                                    trainable=self.embedding_trainable)

            # 1. embedding of words, inputs = [batch_size, sequence_length]
            input_x = tf.split(self.inputs, self.num_sentences, axis=1) # list, each element: [batch_size, sentence_length]
            input_x = tf.stack(input_x, axis=1) # [batch_size, num_sentences, sentence_length], every document represented as [num_sentences, sentence_length]
            print('every document represented as: ', input_x.get_shape().as_list())

            self.embedded_words = tf.nn.embedding_lookup(self.embedding_matrix, input_x) # [batch_size, num_sentences, sentence_length, embedding_dim]
            print('every document embedded as: ', self.embedded_words.get_shape().as_list())

            # batch_num_sentences_length = tf.reshape(self.total_batch_num_sentences, [-1])  # [batch_size * num_sentences]
            embedded_words_reshaped = tf.reshape(self.embedded_words, [-1, self.sentence_length, self.embedding_dim])
            print('word cell input: ', embedded_words_reshaped.get_shape().as_list())

        print('------ word level encode and attention to get sentence representation ------ ')
        with tf.variable_scope('word_encoder'):
            with tf.variable_scope('bigru_encode') as scope:
                word_encoded_outputs, _ = self.bi_gru_encode(embedded_words_reshaped, scope)
                print('word bi-gru encode: ', word_encoded_outputs.get_shape().as_list())

            # word level attention
            with tf.variable_scope('attention') as scope:
                sentences_represented = self.attention(word_encoded_outputs, self.word_attention_size, scope)
                # each sentence encoded size is word_encoder_bigru_num_units, according to bi-gru encoder
                sentences_represented = tf.reshape(sentences_represented,
                                                   shape=[-1, self.num_sentences, 2 * self.word_encoder_bigru_num_units])
                print('sentences_represented: ', sentences_represented.get_shape().as_list())

            with tf.variable_scope('dropout'):
                sentences_represented = layers.dropout(inputs=sentences_represented, keep_prob=self.dropout_keep_prob)

        print('------ sentence level encode and attention to get document representation ------ ')
        with tf.variable_scope('sentence_encoder'):
            with tf.variable_scope('bigru_encode') as scope:
                sentence_encoded_outputs, _ = self.bi_gru_encode(sentences_represented, scope)
                print('sentence bi-gru encode: ', sentence_encoded_outputs.get_shape().as_list())

            with tf.variable_scope('attention') as scope:
                document_represented = self.attention(sentence_encoded_outputs, self.sent_attention_size, scope)
                print('document_represented: ', document_represented.get_shape().as_list())

            with tf.variable_scope('dropout'):
                document_represented = layers.dropout(inputs=document_represented, keep_prob=self.dropout_keep_prob)

        # full connected layer readout
        with tf.name_scope('readout'):
            # 4.linear classifier
            self.W_projection = tf.get_variable(shape=[self.word_encoder_bigru_num_units * 2, self.label_size],
                                                initializer=tf.contrib.layers.xavier_initializer(uniform=True),
                                                name='linear_W_projection')
            self.b_projection = tf.get_variable(shape=[self.label_size],
                                                name='linear_b_projection')
            self.logits = tf.add(tf.matmul(document_represented, self.W_projection), self.b_projection, name='logits')
            print('readout logits: ', self.logits.get_shape().as_list())

        with tf.name_scope("loss"):
            l2_loss = tf.constant(0.0)
            if self.embedding_trainable:
                l2_loss += tf.nn.l2_loss(self.embedding_matrix)

            l2_loss += tf.add_n([tf.nn.l2_loss(v) for v in tf.trainable_variables() if 'bias' not in v.name])

            losses = tf.nn.softmax_cross_entropy_with_logits(logits=self.logits, labels=self.labels)
            self.loss = tf.reduce_mean(losses) + self.l2_reg_lambda * l2_loss

        with tf.name_scope("accuracy"):
            labels = tf.argmax(self.labels, 1)
            self.predictions = tf.argmax(self.logits, 1, name="predictions")
            self.accuracy = tf.reduce_mean(tf.cast(tf.equal(self.predictions, labels), "float"), name="accuracy")

    def bi_gru_encode(self, inputs, scope):
        """
        bi-gru encode words in sentence
        """
        with tf.variable_scope(scope or 'bi_gru_encode'):
            cell_fw = tf.nn.rnn_cell.GRUCell(num_units=self.word_encoder_bigru_num_units)
            cell_bw = tf.nn.rnn_cell.GRUCell(num_units=self.word_encoder_bigru_num_units)

            (encode_outs, encode_states) = tf.nn.bidirectional_dynamic_rnn(cell_fw=cell_fw,
                                                                           cell_bw=cell_bw,
                                                                           inputs=inputs,
                                                                           dtype=tf.float32)
            concated_encode_outs = tf.concat(encode_outs, axis=2)
            concated_encode_states = tf.concat(encode_states, axis=1)

        return concated_encode_outs, concated_encode_states

    def attention(self, inputs, attention_size, scope):
        """
        attention mechanism
        """
        with tf.variable_scope(scope or 'attention',
                               initializer=layers.xavier_initializer(uniform=True),
                               regularizer=layers.l2_regularizer(scale=self.l2_reg_lambda)):
            word_level_context_vector = tf.get_variable(name='attention_context_vector',
                                                        shape=[attention_size],
                                                        dtype=tf.float32)
            # feed the word encoders through a one-layer MLP to get a hidden representation
            hidden_input_represents = layers.fully_connected(inputs=inputs,
                                                             num_outputs=attention_size,
                                                             activation_fn=self.activation,
                                                             weights_regularizer=layers.l2_regularizer(
                                                                 scale=self.l2_reg_lambda))

            # measure the importance of the word as the ** similarity ** of uit with a word level context vector uw
            U_it = self.activation(tf.multiply(hidden_input_represents, word_level_context_vector))
            vector_attn = tf.reduce_sum(U_it, axis=2, keep_dims=True)

            attention_weights = tf.nn.softmax(vector_attn, dim=1)

            weighted_projection = tf.multiply(inputs, attention_weights)
            outputs = tf.reduce_sum(weighted_projection, axis=1)

            return outputs
