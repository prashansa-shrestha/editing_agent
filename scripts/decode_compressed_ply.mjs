import { fstatSync, fsyncSync, writeSync } from 'node:fs';
import { readFile as readFileAsync } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

import { GSplatCompressedData } from 'playcanvas/build/playcanvas/src/scene/gsplat/gsplat-compressed-data.js';

export const CHUNK_SIZE = 256;

export const CHUNK_PROPERTIES = Object.freeze([
    ['float', 'min_x'],
    ['float', 'min_y'],
    ['float', 'min_z'],
    ['float', 'max_x'],
    ['float', 'max_y'],
    ['float', 'max_z'],
    ['float', 'min_scale_x'],
    ['float', 'min_scale_y'],
    ['float', 'min_scale_z'],
    ['float', 'max_scale_x'],
    ['float', 'max_scale_y'],
    ['float', 'max_scale_z'],
    ['float', 'min_r'],
    ['float', 'min_g'],
    ['float', 'min_b'],
    ['float', 'max_r'],
    ['float', 'max_g'],
    ['float', 'max_b']
]);

export const VERTEX_PROPERTIES = Object.freeze([
    ['uint', 'packed_position'],
    ['uint', 'packed_rotation'],
    ['uint', 'packed_scale'],
    ['uint', 'packed_color']
]);

export const SH_PROPERTIES = Object.freeze(
    Array.from({ length: 45 }, (_, index) => ['uchar', `f_rest_${index}`])
);

// This order is the stable Milestone 1A editable representation. In
// particular, it is not the property order used by the compressed source.
export const CANONICAL_PROPERTIES = Object.freeze([
    'x',
    'y',
    'z',
    'f_dc_0',
    'f_dc_1',
    'f_dc_2',
    ...Array.from({ length: 45 }, (_, index) => `f_rest_${index}`),
    'opacity',
    'scale_0',
    'scale_1',
    'scale_2',
    'rot_0',
    'rot_1',
    'rot_2',
    'rot_3'
]);

const END_HEADER = Buffer.from('end_header\n', 'ascii');
const MAX_SAFE_BYTE_LENGTH = Number.MAX_SAFE_INTEGER;

export class DecodeError extends Error {
    constructor(message) {
        super(message);
        this.name = 'DecodeError';
    }
}

const fail = message => {
    throw new DecodeError(message);
};

const parseOutputFd = outputArgument => {
    const value = typeof outputArgument === 'number' ? outputArgument : Number(outputArgument);
    if (!Number.isSafeInteger(value) || value < 3 || String(value) !== String(outputArgument)) {
        fail('output-fd must be an inherited numeric file descriptor of 3 or greater');
    }
    let info;
    try {
        info = fstatSync(value);
    } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        fail(`Cannot inspect inherited output-fd ${value}: ${detail}`);
    }
    if (!info.isFile() || info.size !== 0) {
        fail('output-fd must identify a new, empty regular file');
    }
    if ((info.mode & 0o777) !== 0o600) {
        fail('output-fd must have exact mode 0600');
    }
    return value;
};

const parsePositiveCount = (value, label, lineNumber) => {
    if (!/^\d+$/.test(value)) {
        fail(`Invalid ${label} count '${value}' on header line ${lineNumber}`);
    }
    const count = Number(value);
    if (!Number.isSafeInteger(count) || count <= 0) {
        fail(`Invalid ${label} count '${value}' on header line ${lineNumber}`);
    }
    return count;
};

const describeProperty = ([type, name]) => `${type} ${name}`;

const parsePropertyLine = (line, expected, elementName, lineNumber) => {
    const words = line.split(' ');
    if (words.length !== 3 || words[0] !== 'property') {
        fail(`Unexpected ${elementName} header line ${lineNumber}: '${line}'; expected property ${describeProperty(expected)}`);
    }
    const actual = [words[1], words[2]];
    if (actual[0] !== expected[0] || actual[1] !== expected[1]) {
        fail(`Unexpected ${elementName} property on header line ${lineNumber}: '${line}'; expected property ${describeProperty(expected)}`);
    }
};

const parseElementLine = (line, expectedName, label, lineNumber) => {
    const words = line.split(' ');
    if (words.length !== 3 || words[0] !== 'element' || words[1] !== expectedName) {
        fail(`Unexpected element on header line ${lineNumber}: '${line}'; expected element ${expectedName}`);
    }
    return parsePositiveCount(words[2], label, lineNumber);
};

const parseHeader = source => {
    const markerIndex = source.indexOf(END_HEADER);
    if (markerIndex < 0) {
        fail("Missing 'end_header\\n' marker in PLY header");
    }

    const headerText = source.subarray(0, markerIndex).toString('ascii');
    const rawLines = headerText.split('\n');
    if (rawLines.at(-1) === '') {
        rawLines.pop();
    }
    if (rawLines.length < 2 || rawLines[0] !== 'ply') {
        fail("Invalid PLY header: first line must be 'ply'");
    }

    let cursor = 1;
    const nextMeaningfulLine = () => {
        while (cursor < rawLines.length && rawLines[cursor].startsWith('comment ')) {
            cursor += 1;
        }
        if (cursor >= rawLines.length) {
            fail('PLY header ended before the required compressed schema');
        }
        const result = { line: rawLines[cursor], lineNumber: cursor + 1 };
        cursor += 1;
        return result;
    };

    const formatLine = nextMeaningfulLine();
    if (formatLine.line !== 'format binary_little_endian 1.0') {
        fail(`Unsupported PLY format on header line ${formatLine.lineNumber}: '${formatLine.line}'; expected binary_little_endian 1.0`);
    }

    const chunkLine = nextMeaningfulLine();
    const chunkCount = parseElementLine(chunkLine.line, 'chunk', 'chunk', chunkLine.lineNumber);
    for (const expected of CHUNK_PROPERTIES) {
        const propertyLine = nextMeaningfulLine();
        parsePropertyLine(propertyLine.line, expected, 'chunk', propertyLine.lineNumber);
    }

    const vertexLine = nextMeaningfulLine();
    const vertexCount = parseElementLine(vertexLine.line, 'vertex', 'vertex', vertexLine.lineNumber);
    for (const expected of VERTEX_PROPERTIES) {
        const propertyLine = nextMeaningfulLine();
        parsePropertyLine(propertyLine.line, expected, 'vertex', propertyLine.lineNumber);
    }

    const shLine = nextMeaningfulLine();
    const shCount = parseElementLine(shLine.line, 'sh', 'sh', shLine.lineNumber);
    for (const expected of SH_PROPERTIES) {
        const propertyLine = nextMeaningfulLine();
        parsePropertyLine(propertyLine.line, expected, 'sh', propertyLine.lineNumber);
    }

    while (cursor < rawLines.length) {
        const line = rawLines[cursor];
        const lineNumber = cursor + 1;
        cursor += 1;
        if (line.length === 0 || line.startsWith('comment ')) {
            continue;
        }
        fail(`Unexpected compressed PLY header line ${lineNumber}: '${line}'`);
    }

    const expectedChunkCount = Math.ceil(vertexCount / CHUNK_SIZE);
    if (chunkCount !== expectedChunkCount) {
        fail(`Chunk count ${chunkCount} does not match vertex count ${vertexCount}; expected ${expectedChunkCount}`);
    }
    if (shCount !== vertexCount) {
        fail(`SH count ${shCount} does not match vertex count ${vertexCount}`);
    }

    return {
        markerIndex,
        payloadOffset: markerIndex + END_HEADER.length,
        chunkCount,
        vertexCount,
        shCount
    };
};

const evalStorageSize = count => {
    const width = Math.ceil(Math.sqrt(count));
    const height = Math.ceil(count / width);
    return width * height;
};

const expectedPayloadBytes = ({ chunkCount, vertexCount }) => {
    const chunkBytes = chunkCount * CHUNK_PROPERTIES.length * Float32Array.BYTES_PER_ELEMENT;
    const vertexBytes = vertexCount * VERTEX_PROPERTIES.length * Uint32Array.BYTES_PER_ELEMENT;
    const shBytes = vertexCount * SH_PROPERTIES.length * Uint8Array.BYTES_PER_ELEMENT;
    const total = chunkBytes + vertexBytes + shBytes;
    if (!Number.isSafeInteger(total) || total > MAX_SAFE_BYTE_LENGTH) {
        fail('Compressed payload size exceeds the safe Node.js addressable range');
    }
    return total;
};

const readCompressedPayload = (source, header) => {
    const payloadBytes = source.length - header.payloadOffset;
    const expectedBytes = expectedPayloadBytes(header);
    if (payloadBytes !== expectedBytes) {
        fail(`Compressed payload length mismatch: found ${payloadBytes} bytes, expected ${expectedBytes}; trailing or missing bytes are not allowed`);
    }

    const { chunkCount, vertexCount } = header;
    let offset = header.payloadOffset;
    const chunkData = new Float32Array(chunkCount * CHUNK_PROPERTIES.length);
    for (let index = 0; index < chunkData.length; index += 1) {
        chunkData[index] = source.readFloatLE(offset);
        offset += Float32Array.BYTES_PER_ELEMENT;
        if (!Number.isFinite(chunkData[index])) {
            fail(`Non-finite chunk float at index ${index}`);
        }
    }

    const vertexStorageSize = evalStorageSize(vertexCount);
    const vertexData = new Uint32Array(vertexStorageSize * VERTEX_PROPERTIES.length);
    for (let index = 0; index < vertexCount * VERTEX_PROPERTIES.length; index += 1) {
        vertexData[index] = source.readUInt32LE(offset);
        offset += Uint32Array.BYTES_PER_ELEMENT;
    }

    const shData = new Uint8Array(vertexCount * SH_PROPERTIES.length);
    source.copy(shData, 0, offset, offset + shData.length);
    offset += shData.length;
    if (offset !== source.length) {
        fail(`Internal payload cursor error: stopped at ${offset}, source has ${source.length} bytes`);
    }

    const compressed = new GSplatCompressedData();
    compressed.numSplats = vertexCount;
    compressed.chunkData = chunkData;
    compressed.vertexData = vertexData;
    compressed.shData = shData;
    return compressed;
};

/**
 * Parse a complete compressed PLY into the pinned PlayCanvas compressed data
 * object. No source or output file is opened for writing by this function.
 */
export const parseCompressedPly = source => {
    if (!Buffer.isBuffer(source)) {
        fail('Compressed PLY input must be a Node Buffer');
    }
    const header = parseHeader(source);
    return readCompressedPayload(source, header);
};

const getCanonicalArrays = compressed => {
    let decoded;
    try {
        // This is the exact CPU decompression implementation from the pinned
        // PlayCanvas release. It preserves compressed row order.
        decoded = compressed.decompress();
    } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        fail(`PlayCanvas 2.3.3 decompression failed: ${detail}`);
    }

    if (!decoded || decoded.numSplats !== compressed.numSplats) {
        fail(`Decoded Gaussian count mismatch: expected ${compressed.numSplats}, got ${decoded?.numSplats ?? 'none'}`);
    }
    const element = decoded.getElement('vertex');
    if (!element || element.count !== compressed.numSplats) {
        fail('PlayCanvas decompression did not produce the expected vertex element');
    }

    const arrays = [];
    for (const name of CANONICAL_PROPERTIES) {
        const property = element.properties.find(candidate => candidate.name === name);
        if (!property) {
            fail(`Decoded canonical property '${name}' is missing`);
        }
        if (property.type !== 'float' || property.byteSize !== Float32Array.BYTES_PER_ELEMENT || !(property.storage instanceof Float32Array)) {
            fail(`Decoded canonical property '${name}' is not a float32 array`);
        }
        if (property.storage.length !== compressed.numSplats) {
            fail(`Decoded canonical property '${name}' has ${property.storage.length} rows, expected ${compressed.numSplats}`);
        }
        for (let index = 0; index < property.storage.length; index += 1) {
            if (!Number.isFinite(property.storage[index])) {
                fail(`Decoded canonical property '${name}' contains a non-finite value at row ${index}`);
            }
        }
        arrays.push(property.storage);
    }
    return arrays;
};

const canonicalHeader = vertexCount => {
    const lines = [
        'ply',
        'format binary_little_endian 1.0',
        'comment Milestone 1A canonical partial output; source row order retained',
        `element vertex ${vertexCount}`,
        ...CANONICAL_PROPERTIES.map(name => `property float ${name}`),
        'end_header'
    ];
    return Buffer.from(`${lines.join('\n')}\n`, 'ascii');
};

const writeFully = (fd, buffer) => {
    let offset = 0;
    while (offset < buffer.length) {
        const bytesWritten = writeSync(fd, buffer, offset, buffer.length - offset);
        if (bytesWritten <= 0) {
            fail('Output write made no progress');
        }
        offset += bytesWritten;
    }
};

const writeCanonicalPly = (outputFd, arrays, vertexCount) => {
    writeFully(outputFd, canonicalHeader(vertexCount));

    const rowBytes = CANONICAL_PROPERTIES.length * Float32Array.BYTES_PER_ELEMENT;
    const rowsPerChunk = Math.max(1, Math.floor((1024 * 1024) / rowBytes));
    const body = Buffer.allocUnsafe(rowsPerChunk * rowBytes);
    for (let start = 0; start < vertexCount; start += rowsPerChunk) {
        const rows = Math.min(rowsPerChunk, vertexCount - start);
        let cursor = 0;
        for (let row = 0; row < rows; row += 1) {
            const sourceIndex = start + row;
            for (const array of arrays) {
                body.writeFloatLE(array[sourceIndex], cursor);
                cursor += Float32Array.BYTES_PER_ELEMENT;
            }
        }
        writeFully(outputFd, body.subarray(0, rows * rowBytes));
    }
    fsyncSync(outputFd);
};

/**
 * Decode sourcePath into an already-open descriptor inherited from Python.
 * This module never opens, links, or unlinks an output pathname.
 */
export const decodeCompressedPly = async (sourceArgument, outputArgument) => {
    if (typeof sourceArgument !== 'string' || sourceArgument.length === 0) {
        fail('source is required and must be a path');
    }
    const sourcePath = path.resolve(sourceArgument);
    const outputFd = parseOutputFd(outputArgument);
    let compressedSource;
    try {
        compressedSource = await readFileAsync(sourcePath);
    } catch (error) {
        const detail = error instanceof Error ? error.message : String(error);
        fail(`Cannot read source '${sourceArgument}': ${detail}`);
    }
    const compressed = parseCompressedPly(compressedSource);
    const arrays = getCanonicalArrays(compressed);

    try {
        writeCanonicalPly(outputFd, arrays, compressed.numSplats);
    } catch (error) {
        if (error instanceof DecodeError) {
            throw error;
        }
        const detail = error instanceof Error ? error.message : String(error);
        fail(`Cannot write inherited output-fd ${outputFd}: ${detail}`);
    }

    return {
        source: sourcePath,
        output: `fd:${outputFd}`,
        gaussianCount: compressed.numSplats,
        properties: [...CANONICAL_PROPERTIES],
        propertyType: 'float32',
        rowOrder: 'compressed source vertex order'
    };
};

export const HELP_TEXT = `Usage: node scripts/decode_compressed_ply.mjs <source> <output-fd>

Decode the frozen Milestone 1A compressed Gaussian PLY schema with
PlayCanvas 2.3.3 CPU semantics. output-fd must be an inherited, empty,
mode-0600 regular file created and retained by the Python orchestrator.
`;

const isMainModule = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);

if (isMainModule) {
    const args = process.argv.slice(2);
    if (args.length === 1 && (args[0] === '--help' || args[0] === '-h')) {
        console.log(HELP_TEXT);
    } else if (args.length !== 2) {
        console.error(HELP_TEXT);
        process.exitCode = 2;
    } else {
        decodeCompressedPly(args[0], args[1])
            .then(result => {
                console.log(`Decoded ${result.gaussianCount} Gaussian rows to ${result.output}`);
            })
            .catch(error => {
                const detail = error instanceof Error ? error.message : String(error);
                console.error(`decode_compressed_ply: ${detail}`);
                process.exitCode = 1;
            });
    }
}
