from next_batch_partial import next_batch_partial
from ops import conv2d, conv_transpose, dense, lrelu
from scipy.misc import imsave as ims
from utils import merge
import chocolate as choco
import math
import numpy as np
import os
import os.path
import sys
import tensorflow as tf
import tensorflow.examples.tutorials.mnist.input_data as input_data


class LatentAttention():
    def __init__(self, frac_train, n_z, batchsize, learning_rate, max_epochs,
                 e_h1, e_h2, d_h1, d_h2, run_id):
        """
        frac_train: (0..1) the fraction of the training set to use for
            training ... the rest will be used for validation
        n_z: (positive int) number of latent gaussian variables consumed by
            the decoder / produced by the endcoder
        batchize: (positive int) number of items to include in each training
            minibatch
        learning_rate: (positive float) the learning rate used by the
            optimizer
        max_epochs: number of epochs to run for
        e_h1: (positive integer) number of channels in output of first hidden
            layer in encoder
        e_h2: (positive integer) number of layers in output of second hidden
            layer in encoder
        d_h1: (positive integer) number of channels in input of first hidden
            layer in decoder
        d_h2: (positive integer) number of layers in input of second hidden
            layer in decoder
        run_id: (positive integer) number uniquely identifying the run
        """
        self.mnist = input_data.read_data_sets("MNIST_data/", one_hot=True)
        self.n_train = int(frac_train * self.mnist.train.num_examples)
        self.n_test = self.mnist.train.num_examples - self.n_train
        self.max_epochs = max_epochs
        self.e_h1 = e_h1
        self.e_h2 = e_h2
        self.d_h1 = d_h1
        self.d_h2 = d_h2
        self.run_id = run_id
        self.results_dir = "results_{:04d}".format(self.run_id)
        self.max_epochs_without_improvement = 6
        os.makedirs(self.results_dir, exist_ok=True)

        assert batchsize <= self.n_test

        self.n_z = n_z
        self.batchsize = batchsize

        self.images = tf.placeholder(tf.float32, [None, 784])
        image_matrix = tf.reshape(self.images, [-1, 28, 28, 1])
        z_mean, z_stddev = self.encode(image_matrix)
        samples = tf.random_normal(tf.shape(z_stddev), 0, 1, dtype=tf.float32)
        guessed_z = z_mean + (z_stddev * samples)

        self.generate_images = self.decode(guessed_z)
        generated_flat = tf.reshape(self.generate_images, [-1, 28*28])

        self.diffs = self.images - generated_flat
        self.calc_generation_loss = tf.reduce_sum(tf.square(self.diffs), 1)

        self.calc_latent_loss = 0.5 * tf.reduce_sum(
            tf.square(z_mean) + tf.square(z_stddev) -
            tf.log(tf.square(z_stddev)) - 1, 1)
        self.cost = tf.reduce_mean(
            self.calc_generation_loss + self.calc_latent_loss)
        self.optimizer = tf.train.AdamOptimizer(
            learning_rate).minimize(self.cost)

    def encode(self, input_images):
        with tf.variable_scope("encode"):
            # 28x28x1 -> 14x14x16
            h1 = lrelu(conv2d(input_images, 1, self.e_h1, "e_h1"))
            # 14x14x16 -> 7x7x32
            h2 = lrelu(conv2d(h1, self.e_h1, self.e_h2, "e_h2"))
            h2_flat = tf.reshape(h2, [-1, 7*7*self.e_h2])

            w_mean = dense(h2_flat, 7*7*self.e_h2, self.n_z, "w_mean")
            w_stddev = dense(h2_flat, 7*7*self.e_h2, self.n_z, "w_stddev")

        return w_mean, w_stddev

    def decode(self, z):
        with tf.variable_scope("decode"):
            z_shape = tf.shape(z)
            z_develop = dense(z, self.n_z, 7*7*self.d_h1, scope='z_matrix')
            z_matrix = tf.nn.relu(tf.reshape(z_develop, [-1, 7, 7, self.d_h1]))
            h1 = tf.nn.relu(conv_transpose(
                z_matrix, [z_shape[0], 14, 14, self.d_h2], "d_h1"))
            h2 = conv_transpose(h1, [z_shape[0], 28, 28, 1], "d_h2")
            h2 = tf.nn.sigmoid(h2)

        return h2

    def print_epoch(self, epoch, gen_loss, lat_loss, saver, sess,
                    validation):

        saver.save(sess, os.path.join(self.results_dir, 'checkpoints',
                                      'checkpoint'),
                   global_step=epoch)
        val_ims, val_error = sess.run(
            [self.generate_images, self.calc_generation_loss],
            feed_dict={self.images: validation})
        fn="{:04d}.jpg".format(epoch)
        ims(os.path.join(self.results_dir, fn),
            merge(val_ims.reshape(-1, 28, 28)[:64], [8, 8]))

        self.validation_error = float(np.mean(val_error))
        print("epoch {:02d}: genloss {:7.3f} latloss {:7.3f} "
              "validation_genloss {:7.3f}".format(
                  epoch,
                  np.mean(gen_loss), np.mean(lat_loss), self.validation_error))

        if self.best is None or self.validation_error < self.best:
            self.best_epoch = epoch
            self.best = self.validation_error

    def train(self):
        self.validation_error = 100000.0
        try:
            data = self.mnist.train
            if self.n_test == 0:
                validation, val_labels = next_batch_partial(
                    data, self.batchsize, self.n_train)
            else:
                validation = data.images[self.n_train:]
                val_labels = data.labels[self.n_train:]

            reshaped_val = validation.reshape(-1, 28, 28)
            ims(os.path.join(self.results_dir, "base.jpg"),
                merge(reshaped_val[:64], [8, 8]))
            # train
            self.best = None
            self.best_epoch = 0
            saver = tf.train.Saver(max_to_keep=2)
            with tf.Session() as sess:
                sess.run(tf.global_variables_initializer())
                last_epochs_completed = -1
                while(
                        data.epochs_completed < self.max_epochs and
                        data.epochs_completed - self.best_epoch <
                        self.max_epochs_without_improvement
                ):
                    if math.isnan(float(self.validation_error)):
                        # Quit early on nan since it will just propagate
                        # and be the final result anyway
                        break
                    batch, batch_labels = next_batch_partial(
                        data, self.batchsize, self.n_train)
                    _, gen_loss, lat_loss = sess.run(
                        (self.optimizer, self.calc_generation_loss,
                         self.calc_latent_loss),
                        feed_dict={self.images: batch})
                    if last_epochs_completed != data.epochs_completed:
                        last_epochs_completed = data.epochs_completed
                        self.print_epoch(
                            last_epochs_completed, gen_loss, lat_loss,
                            saver, sess, validation
                        )
        except Exception:
            print("Exception occurred.")
            self.validation_error = 100000.0

if __name__ == '__main__':
    if len(sys.argv) == 1:
        # original params from article
        model=LatentAttention(frac_train=0.99, n_z=20, batchsize=100,
                              learning_rate=0.001, max_epochs=10,
                              e_h1=16, e_h2=32, d_h1=32, d_h2=16, run_id=-1);
        model.train()
        print("loss={}".format(float(model.validation_error)))
        exit(0)

    # Params from optimizer
    search_space = {
        "n_z": choco.quantized_uniform(5, 100, 1),
        "learning_rate": choco.log(-20, -8, 2),
        "max_epochs": choco.quantized_uniform(5, 200, 1),
        "e_h1": choco.quantized_uniform(16, 256, 1),
        "e_h2": choco.quantized_uniform(16, 256, 1),
        "d_h1": choco.quantized_uniform(16, 256, 1),
        "d_h2": choco.quantized_uniform(16, 256, 1),
    }
    connection = choco.SQLiteConnection("sqlite:///no_labels_results.sqlite3")
    sampler = choco.Bayes(connection, search_space)
    token, sample = sampler.next()
    print("Parameters: {} Token: {}".format(sample, token))
    run_id = token['_chocolate_id']
    model = LatentAttention(0.99, batchsize=150, run_id=run_id, **sample)
    model.train()
    sampler.update(token, float(model.validation_error))
