from numpy import asarray, ndarray, prod, ufunc, ScalarType, add, subtract, multiply, divide
from bolt.utils import inshape, tupleize
from bolt.base import BoltArray
from bolt.spark.array import BoltArraySpark
from bolt.spark.chunk import ChunkedArray


from .utils import notsupported

class Base(object):
    """
    Base methods for data objects in thunder.

    Data objects are backed by array-like objects,
    including numpy arrays (for local computation),
    and bolt arrays (for spark computation).

    Handles construction, metadata, and backend specific methods.
    """
    _metadata = ['dtype', 'shape', 'mode']
    _attributes = []

    def __init__(self, values, mode='local'):
        self._values = values
        if isinstance(values, BoltArray) or isinstance(values, ChunkedArray):
            mode = 'spark'
        if isinstance(values, ndarray):
            mode = 'local'
        self._mode = mode

    def __repr__(self):
        s = self.__class__.__name__
        s += '\n%s: %s' % ('mode', getattr(self, 'mode'))
        for k in self._metadata:
            v = getattr(self, k)
            output = str(v)
            if len(output) > 50:
                output = output[0:50].strip() + ' ... '
                if output.lstrip().startswith('['):
                    output += '] '
                if hasattr(v, '__len__'):
                    output += '(length: %d)' % len(v)
            if not k == 'mode':
                s += '\n%s: %s' % (k, output)
        return s

    def __finalize__(self, other, noprop=()):
        """
        Lazily propagate attributes from other to self, only if attributes
        are not already defined in self

        Parameters
        ----------
        other : the object from which to get the attributes that we are going
            to propagate

        noprop : iterable of string attribute names, default empty tuple
            Attributes found in noprop will *not* have their values propagated forward.
        """
        if isinstance(other, Data):
            for name in self._attributes:
                if name not in noprop:
                    attr = getattr(other, name, None)
                    if attr is not None:
                        object.__setattr__(self, name, attr)
        return self

    @property
    def _constructor(self):
        return Data

    @property
    def dtype(self):
        return self._values.dtype

    @property
    def shape(self):
        return self._values.shape

    @property
    def mode(self):
        return self._mode

    @property
    def values(self):
        return self._values

    def tordd(self):
        """
        Return an RDD for datasets backed by Spark
        """
        if self.mode == 'spark':
            return self.values.tordd()
        else:
            notsupported(self.mode)

    def compute(self):
        """
        Force lazy computations to execute for datasets backed by Spark.
        """
        if self.mode == 'spark':
            self.values.tordd().count()
        else:
            notsupported(self.mode)

    def coalesce(self, npartitions):
        """
        Coalesce data (Spark only).

        Parameters
        ----------
        npartitions : int
            Number of partitions after coalescing.
        """
        if self.mode == 'spark':
            current = self.values.tordd().getNumPartitions()
            if npartitions > current:
                raise Exception('Trying to increase number of partitions (from %g to %g), '
                                'cannot use coalesce, try repartition' % (current, npartitions))
            self.values._rdd = self.values._rdd.coalesce(npartitions)
            return self
        else:
            notsupported(self.mode)

    def cache(self):
        """
        Enable in-memory caching.
        """
        if self.mode == 'spark':
            self.values.cache()
            return self
        else:
            notsupported(self.mode)

    def uncache(self):
        """
        Enable in-memory caching.
        """
        if self.mode == 'spark':
            self.values.unpersist()
            return self
        else:
            notsupported(self.mode)

    def repartition(self, npartitions):
        """
        Repartition data (Spark only).

        Parameters
        ----------
        npartitions : int
            Number of partitions after repartitions.
        """
        if self.mode == 'spark':
            self.values._rdd = self.values._rdd.repartition(npartitions)
            return self
        else:
            notsupported(self.mode)

class Data(Base):
    """
    Generic base class for primary data types in Thunder.

    All data types are backed by an array-like container for many homogenous arrays.
    Data types include Images and Series and their derivatives.

    This class mainly provides convenience functions for accessing
    properties, computing generic summary statistics, and applying
    functions along axes in a backend-specific manner.
    """
    _metadata = Base._metadata

    def __getitem__(self, item):
        new = self._values.__getitem__(item)
        # if first index is a singleton, return a local array
        # if all remaining indices are singleton, for series, set index
        return self._constructor(new).__finalize__(self, noprop=('index'))

    def astype(self, dtype, casting='unsafe'):
        """
        Cast values to the specified type.
        """
        return self._constructor(
            self.values.astype(dtype=dtype, casting=casting)).__finalize__(self)

    def toarray(self):
        """
        Return all records to the driver as a numpy array

        This will be slow for large datasets, and may exhaust the available memory on the driver.
        """
        return asarray(self.values).squeeze()

    def tospark(self):
        """
        Convert data to Spark.
        """
        raise NotImplementedError

    def tolocal(self):
        """
        Convert data to local mode.
        """
        raise NotImplementedError

    def count(self):
        """
        Explicit count of elements.
        """
        raise NotImplementedError

    def first(self):
        """
        Return first element.
        """
        raise NotImplementedError

    def mean(self):
        """
        Mean of values computed along the appropriate dimension.
        """
        raise NotImplementedError

    def sum(self):
        """
        Sum of values computed along the appropriate dimension.
        """
        raise NotImplementedError

    def var(self):
        """
        Variance of values computed along the appropriate dimension.
        """
        raise NotImplementedError

    def std(self):
        """
        Standard deviation computed of values along the appropriate dimension.
        """
        raise NotImplementedError

    def max(self):
        """
        Maximum of values computed along the appropriate dimension.
        """
        raise NotImplementedError

    def min(self):
        """
        Minimum of values computed along the appropriate dimension.
        """
        raise NotImplementedError

    def filter(self, func):
        """
        Filter elements.
        """
        raise NotImplementedError

    def map(self, func, **kwargs):
        """
        Map a function over elements.
        """
        raise NotImplementedError

    def _align(self, axes, key_shape=None):
        """
        Align local arrays so that axes for iteration are in the keys.

        This operation is applied before most functional operators.
        It ensures that the specified axes are valid, and might transpose/reshape
        the underlying array so that the functional operators can be applied
        over the correct records.

        Parameters
        ----------
        axes: tuple[int]
            One or more axes that will be iterated over by a functional operator
        """
        # ensure that the key axes are valid for an ndarray of this shape
        inshape(self.shape, axes)

        # compute the set of dimensions/axes that will be used to reshape
        remaining = [dim for dim in range(len(self.shape)) if dim not in axes]
        key_shape = key_shape if key_shape else [self.shape[axis] for axis in axes]
        remaining_shape = [self.shape[axis] for axis in remaining]
        linearized_shape = [prod(key_shape)] + remaining_shape

        # compute the transpose permutation
        transpose_order = axes + remaining

        # transpose the array so that the keys being mapped over come first, then linearize keys
        reshaped = self.values.transpose(*transpose_order).reshape(*linearized_shape)

        return reshaped

    def _filter(self, func, axis=(0,)):
        """
        Filter array along an axis.

        Applies a function which should evaluate to boolean,
        along a single axis or multiple axes. Array will be
        aligned so that the desired set of axes are in the
        keys, which may require a transpose/reshape.

        Parameters
        ----------
        func : function
            Function to apply, should return boolean

        axis : tuple or int, optional, default=(0,)
            Axis or multiple axes to filter along.
        """
        if self.mode == 'local':
            axes = sorted(tupleize(axis))
            reshaped = self._align(axes)
            filtered = asarray(list(filter(func, reshaped)))
            return self._constructor(filtered).__finalize__(self)

        if self.mode == 'spark':
            filtered = self.values.filter(func)
            return self._constructor(filtered).__finalize__(self)

    def _map(self, func, axis=(0,), value_shape=None):
        """
        Apply a function across an axis.

        Array will be aligned so that the desired set of axes
        are in the keys, which may require a transpose/reshape.

        Parameters
        ----------
        func : function
            Function of a single array to apply

        axis : tuple or int, optional, default=(0,)
            Axis or multiple axes to apply function along.
        """
        if self.mode == 'local':
            axes = sorted(tupleize(axis))
            key_shape = [self.shape[axis] for axis in axes]
            reshaped = self._align(axes, key_shape=key_shape)

            mapped = asarray(list(map(func, reshaped)))
            elem_shape = mapped[0].shape
            expand = list(elem_shape)
            expand = [1] if len(expand) == 0 else expand

            # invert the previous reshape operation, using the shape of the map result
            linearized_shape_inv = key_shape + expand
            reordered = mapped.reshape(*linearized_shape_inv)

            return self._constructor(reordered, mode=self.mode).__finalize__(self, noprop=('index'))

        if self.mode == 'spark':
            mapped = self.values.map(func, axis, value_shape)
            return self._constructor(mapped, mode=self.mode).__finalize__(self, noprop=('index'))

    def _reduce(self, func, axis=0):
        """
        Reduce an array along an axis.

        Applies an associative/commutative function of two arguments
        cumulatively to all arrays along an axis. Array will be aligned
        so that the desired set of axes are in the keys, which may
        require a transpose/reshape.

        Parameters
        ----------
        func : function
            Function of two arrays that returns a single array

        axis : tuple or int, optional, default=(0,)
            Axis or multiple axes to reduce along.
        """
        if self.mode == 'local':
            axes = sorted(tupleize(axis))

            # if the function is a ufunc, it can automatically handle reducing over multiple axes
            if isinstance(func, ufunc):
                inshape(self.shape, axes)
                reduced = func.reduce(self, axis=tuple(axes))
            else:
                reshaped = self._align(axes)
                reduced = reduce(func, reshaped)

            new_array = self._constructor(reduced)

            # ensure that the shape of the reduced array is valid
            expected_shape = [self.shape[i] for i in range(len(self.shape)) if i not in axes]
            if new_array.shape != tuple(expected_shape):
                raise ValueError("reduce did not yield an array with valid dimensions")

            return self._constructor(new_array).__finalize__(self)

        if self.mode == 'spark':
            reduced = self.values.reduce(func, axis)
            return self._constructor(reduced).__finalize__(self)

    def element_wise(self, other, op):
        """
        Apply an elementwise operation to data.

        Both self and other data must have the same mode.
        If self is in local mode, other can also be a numpy array.

        Parameters
        ----------
        other : Data or numpy array
           Data to apply elementwise operation to

        op : function
            Binary operator to use for elementwise operations, e.g. add, subtract
        """
        if not self.shape == other.shape:
            raise ValueError("shapes %s and %s must be equal" % (self.shape, other.shape))

        if isinstance(other, Data) and not self.mode == other.mode:
            raise NotImplementedError

        if self.mode == 'local' and isinstance(other, (ndarray, ScalarType)):
            return self._constructor(op(self.values, other)).__finalize__(self)

        if self.mode == 'local' and isinstance(other, Data):
            return self._constructor(op(self.values, other.values)).__finalize__(self)

        if self.mode == 'spark' and isinstance(other, Data):
            func = lambda ((k1, x), (k2, y)): (k1, op(x, y))
            rdd = self.tordd().zip(other.tordd()).map(func)
            barray = BoltArraySpark(rdd, shape=self.shape, dtype=self.dtype)
            return self._constructor(barray).__finalize__(self)

    def plus(self, other):
        """
        Elementwise addition.

        See also
        --------
        elementwise
        """
        return Data.element_wise(self, other, add)

    def minus(self, other):
        """
        Elementwise subtraction.

        See also
        --------
        elementwise
        """
        return Data.element_wise(self, other, subtract)

    def dottimes(self, other):
        """
        Elementwise multiplication.

        See also
        --------
        elementwise
        """
        return Data.element_wise(self, other, multiply)

    def dotdivide(self, other):
        """
        Elementwise divison.

        See also
        --------
        elementwise
        """
        return Data.element_wise(self, other, divide)