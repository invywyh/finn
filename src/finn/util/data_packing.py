import numpy as np
import sys

from bitstring import BitArray
from finn.core.datatype import DataType

def array2hexstring(array, dtype, pad_to_nbits, prefix="0x"):
    """
    Pack given one-dimensional NumPy array with FINN DataType dtype into a hex
    string.
    Any BIPOLAR values will be converted to a single bit with a 0 representing
    -1.
    pad_to_nbits is used to prepend leading zeros to ensure packed strings of
    fixed width. The minimum value for pad_to_nbits is 4, since a single hex
    digit is four bits.

    Examples:
    array2hexstring([1, 1, 1, 0], DataType.BINARY, 4) = "e"
    array2hexstring([1, 1, 1, 0], DataType.BINARY, 8) = "0e"
    """
    if pad_to_nbits < 4:
        pad_to_nbits = 4
    # ensure input is a numpy array with float values
    if type(array) != np.ndarray or array.dtype != np.float32:
        # try to convert to a float numpy array (container dtype is float)
        array = np.asarray(array, dtype=np.float32)
    # ensure one-dimensional array to pack
    assert array.ndim == 1
    if dtype == DataType.BIPOLAR:
        # convert bipolar values to binary
        array = (array + 1) / 2
        dtype = DataType.BINARY
    lineval = BitArray(length=0)
    bw = dtype.bitwidth()
    for val in array:
        # ensure that this value is permitted by chosen dtype
        assert dtype.allowed(val)
        if dtype.is_integer():
            if dtype.signed():
                lineval.append(BitArray(int=int(val), length=bw))
            else:
                lineval.append(BitArray(uint=int(val), length=bw))
        else:
            lineval.append(BitArray(float=val, length=bw))
    if pad_to_nbits >= lineval.len:
        # extend to the desired output width (a minimum of 4 bits)
        lineval.prepend(BitArray(length=pad_to_nbits - lineval.len))
    else:
        raise Exception("Number of bits is greater than pad_to_nbits")
    # represent as hex
    return prefix + lineval.hex


def pack_innermost_dim_as_hex_string(ndarray, dtype, pad_to_nbits):
    """Pack the innermost dimension of the given numpy ndarray into hex
    strings using array2hexstring. Examples:

    A = [[1, 1, 1, 0], [0, 1, 1, 0]]
    eA = ["0e", "06"]
    pack_innermost_dim_as_hex_string(A, DataType.BINARY, 8) == eA
    B = [[[3, 3], [3, 3]], [[1, 3], [3, 1]]]
    eB = [[ "0f", "0f"], ["07", "0d"]]
    pack_innermost_dim_as_hex_string(B, DataType.UINT2, 8) == eB
    """

    if type(ndarray) != np.ndarray or ndarray.dtype != np.float32:
        # try to convert to a float numpy array (container dtype is float)
        ndarray = np.asarray(ndarray, dtype=np.float32)

    def fun(x):
        return array2hexstring(x, dtype, pad_to_nbits)

    return np.apply_along_axis(fun, ndarray.ndim - 1, ndarray)


def unpack_innermost_dim_from_hex_string(
    data, dtype, shape, packedBits, targetBits, rtlsim=False
):
    # function expects flattens array and returns an array in the desired shape
    outer_dim_elems = 1
    for dim in range(len(shape) - 1):
        outer_dim_elems = outer_dim_elems * shape[dim]
    inner_dim_elems = shape[-1]

    array = []
    for outer_elem in range(outer_dim_elems):
        ar_list = []
        ar_elem = data[0]
        data.pop(0)
        ar_elem = ar_elem.split("x")
        ar_elem_bin = bin(int(ar_elem[1], 16))[2:].zfill(packedBits)
        ar_elem_bin = [int(x) for x in ar_elem_bin]

        ar_elem_bin.reverse()
        for i in range(inner_dim_elems):
            upper_limit = (i + 1) * targetBits
            lower_limit = i * targetBits
            elem = ar_elem_bin[lower_limit:upper_limit]
            elem.reverse()
            elem_str = "".join(map(str, elem))
            ar_list.append(int(elem_str, 2))
        # reverse inner dimension back to "normal" positions
        if rtlsim is False:
            ar_list.reverse()
        else:
            # interpret output values correctly by flattening and adjusting the output
            if dtype == DataType.BIPOLAR:
                ar_list = [2 * x - 1 for x in ar_list]
            # pyverilator interprets int2 as uint2, so output has to be corrected
            elif dtype == DataType.INT2 or dtype == DataType.INT32:
                mask = 2 ** (dtype.bitwidth() - 1)
                ar_list = [-(x & mask) + (x & ~mask) for x in ar_list]

        array.append(ar_list)
    array = np.asarray(array, dtype=np.float32).reshape(shape)
    return array

def numpy_to_hls_code(
    ndarray, dtype, hls_var_name, pack_innermost_dim=True, no_decl=False
):
    """Return C++ code representation of a numpy ndarray with FINN DataType
    dtype, using hls_var_name as the resulting C++ variable name. If
    pack_innermost_dim is specified, the innermost dimension of the ndarray
    will be packed into a hex string using array2hexstring. If no_decl is
    set to True, no variable name and type will be generated as part of the
    emitted string.
    """
    hls_dtype = dtype.get_hls_datatype_str()
    if type(ndarray) != np.ndarray or ndarray.dtype != np.float32:
        # try to convert to a float numpy array (container dtype is float)
        ndarray = np.asarray(ndarray, dtype=np.float32)
    if pack_innermost_dim:
        idimlen = ndarray.shape[-1]
        idimbits = idimlen * dtype.bitwidth()
        ndarray = pack_innermost_dim_as_hex_string(ndarray, dtype, idimbits)
        hls_dtype = "ap_uint<%d>" % idimbits
    ndims = ndarray.ndim
    # add type string and variable name
    # e.g. "const ap_uint<64>" "weightMem0"
    ret = "%s %s" % (hls_dtype, hls_var_name)
    # add dimensions
    for d in range(ndims):
        ret += "[%d]" % ndarray.shape[d]
    orig_printops = np.get_printoptions()
    np.set_printoptions(threshold=sys.maxsize)

    # define a function to convert a single element into a C++ init string
    # a single element can be a hex string if we are using packing
    def elem2str(x):
        if type(x) == str or type(x) == np.str_ or type(x) == np.str:
            return '%s("%s", 16)' % (hls_dtype, x)
        elif type(x) == np.float32:
            if dtype == DataType.FLOAT32:
                return str(x)
            else:
                return str(int(x))
        else:
            raise Exception("Unsupported type for numpy_to_hls_code")

    strarr = np.array2string(ndarray, separator=", ", formatter={"all": elem2str})
    np.set_printoptions(**orig_printops)
    strarr = strarr.replace("[", "{").replace("]", "}")
    if no_decl:
        ret = strarr + ";"
    else:
        ret = ret + " = \n" + strarr + ";"
    return ret


def npy_to_rtlsim_input(input_file, input_dtype, pad_to_nbits):
    """Convert the multidimensional NumPy array of integers (stored as floats)
    from input_file into a flattened sequence of Python arbitrary-precision
    integers, packing the innermost dimension. See
    finn.util.basic.pack_innermost_dim_as_hex_string() for more info on how the
    packing works."""

    inp = np.load(input_file)
    ishape = inp.shape
    inp = inp.flatten()
    inp_rev = []
    for i in range(len(inp)):
        inp_rev.append(inp[-1])
        inp = inp[:-1]
    inp_rev = np.asarray(inp_rev, dtype=np.float32).reshape(ishape)
    packed_data = pack_innermost_dim_as_hex_string(inp_rev, input_dtype, pad_to_nbits)
    packed_data = packed_data.flatten()
    packed_data = [int(x[2:], 16) for x in packed_data]
    packed_data.reverse()
    return packed_data


def rtlsim_output_to_npy(output, path, dtype, shape, packedBits, targetBits):
    """Convert a flattened sequence of Python arbitrary-precision integers
    output into a NumPy array, saved as npy file at path. Each arbitrary-precision
    integer is assumed to be a packed array of targetBits-bit elements, which
    will be unpacked as the innermost dimension of the NumPy array."""

    output = [hex(int(x)) for x in output]
    out_array = unpack_innermost_dim_from_hex_string(
        output, dtype, shape, packedBits, targetBits, True
    )
    np.save(path, out_array)