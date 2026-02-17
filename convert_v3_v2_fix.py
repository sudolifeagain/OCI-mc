import gzip, struct, io, sys, math

class NBTReader:
    def __init__(self, data):
        self.data = data; self.pos = 0
    def read(self, n):
        r = self.data[self.pos:self.pos+n]; self.pos += n; return r
    def read_ubyte(self): return struct.unpack('>B', self.read(1))[0]
    def read_short(self): return struct.unpack('>h', self.read(2))[0]
    def read_int(self): return struct.unpack('>i', self.read(4))[0]
    def read_long(self): return struct.unpack('>q', self.read(8))[0]
    def read_float(self): return struct.unpack('>f', self.read(4))[0]
    def read_double(self): return struct.unpack('>d', self.read(8))[0]
    def read_string(self):
        l = struct.unpack('>H', self.read(2))[0]
        return self.read(l).decode('utf-8', errors='replace')
    def read_payload(self, t):
        if t == 1: return ('byte', self.read_ubyte())
        elif t == 2: return ('short', self.read_short())
        elif t == 3: return ('int', self.read_int())
        elif t == 4: return ('long', self.read_long())
        elif t == 5: return ('float', self.read_float())
        elif t == 6: return ('double', self.read_double())
        elif t == 7:
            l = self.read_int(); return ('byte_array', l, self.read(l))
        elif t == 8: return ('string', self.read_string())
        elif t == 9:
            lt = self.read_ubyte(); c = self.read_int()
            return ('list', lt, [self.read_payload(lt) for _ in range(c)])
        elif t == 10:
            entries = []
            while True:
                ct = self.read_ubyte()
                if ct == 0: break
                cn = self.read_string(); cv = self.read_payload(ct)
                entries.append((ct, cn, cv))
            return ('compound', entries)
        elif t == 11:
            l = self.read_int(); return ('int_array', l, [self.read_int() for _ in range(l)])
        elif t == 12:
            l = self.read_int(); return ('long_array', l, [self.read_long() for _ in range(l)])

class NBTWriter:
    def __init__(self):
        self.buf = io.BytesIO()
    def write(self, d): self.buf.write(d)
    def write_ubyte(self, v): self.write(struct.pack('>B', v))
    def write_short(self, v): self.write(struct.pack('>h', v))
    def write_int(self, v): self.write(struct.pack('>i', v))
    def write_long(self, v): self.write(struct.pack('>q', v))
    def write_float(self, v): self.write(struct.pack('>f', v))
    def write_double(self, v): self.write(struct.pack('>d', v))
    def write_string(self, s):
        e = s.encode('utf-8'); self.write(struct.pack('>H', len(e))); self.write(e)
    def write_payload(self, t, v):
        if t == 1: self.write_ubyte(v[1])
        elif t == 2: self.write_short(v[1])
        elif t == 3: self.write_int(v[1])
        elif t == 4: self.write_long(v[1])
        elif t == 5: self.write_float(v[1])
        elif t == 6: self.write_double(v[1])
        elif t == 7: self.write_int(v[1]); self.write(v[2])
        elif t == 8: self.write_string(v[1])
        elif t == 9:
            self.write_ubyte(v[1]); self.write_int(len(v[2]))
            for item in v[2]: self.write_payload(v[1], item)
        elif t == 10:
            for ct, cn, cv in v[1]:
                self.write_ubyte(ct); self.write_string(cn); self.write_payload(ct, cv)
            self.write_ubyte(0)
        elif t == 11:
            self.write_int(v[1])
            for i in v[2]: self.write_int(i)
        elif t == 12:
            self.write_int(v[1])
            for i in v[2]: self.write_long(i)
    def get_bytes(self): return self.buf.getvalue()

def find_tag(compound, name):
    for ct, cn, cv in compound[1]:
        if cn == name: return ct, cv
    return None, None

def convert_item(item_entries):
    """Convert 1.21.4 item to 1.20.1: count(Int)->Count(Byte), remove components"""
    new = []
    for ict, icn, icv in item_entries:
        if icn == 'count':
            val = min(127, max(0, icv[1]))
            new.append((1, 'Count', ('byte', val)))
        elif icn == 'components':
            continue
        else:
            new.append((ict, icn, icv))
    return new

def convert_items_list(items_val):
    if items_val[0] != 'list':
        return items_val
    new_items = []
    for item in items_val[2]:
        if item[0] == 'compound':
            new_items.append(('compound', convert_item(item[1])))
        else:
            new_items.append(item)
    return ('list', items_val[1], new_items)

# Paper/Bukkit/Spigot固有タグ（Forge非互換）
PAPER_TAGS = {
    'Paper.SpawnReason', 'Paper.Origin', 'Paper.OriginWorld', 'Paper.ShouldBurnInDay',
    'Bukkit.updateLevel', 'Bukkit.Aware', 'Spigot.ticksLived',
    'WorldUUIDMost', 'WorldUUIDLeast',
}

def convert_entity_nbt(entries):
    """Convert entity NBT from 1.21+ to 1.20.1 format.

    - block_pos (IntArray[3]) -> TileX, TileY, TileZ (separate Int tags)
      block_pos contains absolute world coords; derive from relative Pos instead
    - Item compound: count->Count, strip components
    - Strip Paper/Bukkit/Spigot specific tags
    """
    new = []
    has_block_pos = False
    pos_values = None
    for ct, cn, cv in entries:
        if cn in PAPER_TAGS:
            continue
        if cn == 'Pos' and cv[0] == 'list' and len(cv[2]) >= 3:
            pos_values = [item[1] for item in cv[2]]
        if cn == 'block_pos' and cv[0] == 'int_array' and cv[1] == 3:
            has_block_pos = True
            continue
        if cn == 'Item' and cv[0] == 'compound':
            new.append((ct, cn, ('compound', convert_item(cv[1]))))
            continue
        new.append((ct, cn, cv))
    if has_block_pos and pos_values is not None:
        new.append((3, 'TileX', ('int', math.floor(pos_values[0]))))
        new.append((3, 'TileY', ('int', math.floor(pos_values[1]))))
        new.append((3, 'TileZ', ('int', math.floor(pos_values[2]))))
    return new

def convert_block_entity_inner(entries):
    new = []
    for ect, ecn, ecv in entries:
        if ecn == 'Items' and ecv[0] == 'list':
            new.append((ect, ecn, convert_items_list(ecv)))
        elif ecn == 'components':
            continue
        else:
            new.append((ect, ecn, ecv))
    return new

def convert_v3_to_v2(input_path, output_path):
    """Convert Sponge Schematic v3 to v2."""
    print(f'Input:  {input_path}')
    print(f'Output: {output_path}')

    with gzip.open(input_path, 'rb') as f:
        data = f.read()

    reader = NBTReader(data)
    rt = reader.read_ubyte()
    rn = reader.read_string()
    root = reader.read_payload(rt)

    # v3: Root("") -> Schematic compound; v2: Root("Schematic") -> direct
    if rn == '' or rn == 'Schematic':
        _, schem = find_tag(root, 'Schematic')
        if schem is None:
            schem = root
    else:
        schem = root

    _, ver = find_tag(schem, 'Version')
    if ver:
        print(f'Source version: {ver[1]}')

    v2_entries = []
    for ct, cn, cv in schem[1]:
        if cn == 'Version':
            v2_entries.append((3, 'Version', ('int', 2)))
            print('Version -> 2')
        elif cn == 'Entities':
            if cv[0] == 'list' and cv[2]:
                converted_entities = []
                for entity in cv[2]:
                    if entity[0] != 'compound':
                        converted_entities.append(entity)
                        continue
                    entity_map = {ecn: (ect, ecv) for ect, ecn, ecv in entity[1]}
                    ne = []
                    outer_pos_vals = None
                    inner_pos_vals = None
                    inner_block_pos = None
                    if 'Id' in entity_map:
                        ect, ecv = entity_map['Id']; ne.append((ect, 'Id', ecv))
                    if 'Pos' in entity_map:
                        ect, ecv = entity_map['Pos']
                        if ecv[0] == 'list' and len(ecv[2]) >= 3:
                            outer_pos_vals = [item[1] for item in ecv[2]]
                        ne.append((ect, 'Pos', ecv))
                    if 'Data' in entity_map:
                        ect, ecv = entity_map['Data']
                        if ecv[0] == 'compound':
                            for dct, dcn, dcv in ecv[1]:
                                if dcn == 'id':
                                    continue
                                if dcn == 'Pos' and dcv[0] == 'list' and len(dcv[2]) >= 3:
                                    inner_pos_vals = [item[1] for item in dcv[2]]
                                    continue
                                if dcn == 'block_pos' and dcv[0] == 'int_array' and dcv[1] == 3:
                                    inner_block_pos = dcv[2]
                                ne.append((dct, dcn, dcv))
                    # Reconstruct precise relative Pos from absolute Data.Pos
                    if inner_pos_vals and inner_block_pos and outer_pos_vals:
                        offset = [inner_block_pos[i] - math.floor(outer_pos_vals[i]) for i in range(3)]
                        corrected = [inner_pos_vals[i] - offset[i] for i in range(3)]
                        for idx, (t, n, v) in enumerate(ne):
                            if n == 'Pos':
                                ne[idx] = (9, 'Pos', ('list', 6, [('double', p) for p in corrected]))
                                break
                    ne = convert_entity_nbt(ne)
                    converted_entities.append(('compound', ne))
                v2_entries.append((9, 'Entities', ('list', 10, converted_entities)))
                print(f'Entities: {len(converted_entities)} converted')
            else:
                print('Entities: 0')

        elif cn == 'Blocks':
            bd = {bcn: (bct, bcv) for bct, bcn, bcv in cv[1]}
            if 'Palette' in bd:
                bt, bv = bd['Palette']
                v2_entries.append((bt, 'Palette', bv))
                ps = len(bv[1]) if bv[0] == 'compound' else 0
                v2_entries.append((3, 'PaletteMax', ('int', ps)))
                print(f'Palette: {ps} entries')
            if 'Data' in bd:
                bt, bv = bd['Data']
                v2_entries.append((bt, 'BlockData', bv))
                print('Blocks.Data -> BlockData')
            if 'BlockEntities' in bd:
                bt, bv = bd['BlockEntities']
                converted = []
                items_count = 0
                for entity in bv[2]:
                    em = {ecn: (ect, ecv) for ect, ecn, ecv in entity[1]}
                    ne = []
                    if 'Id' in em:
                        ect, ecv = em['Id']; ne.append((ect, 'Id', ecv))
                    if 'Pos' in em:
                        ect, ecv = em['Pos']; ne.append((ect, 'Pos', ecv))
                    if 'Data' in em:
                        ect, ecv = em['Data']
                        if ecv[0] == 'compound':
                            inner = convert_block_entity_inner(ecv[1])
                            for dct, dcn, dcv in inner:
                                if dcn == 'id':
                                    continue
                                ne.append((dct, dcn, dcv))
                            if any(dcn == 'Items' for _, dcn, _ in inner):
                                items_count += 1
                    converted.append(('compound', ne))
                v2_entries.append((9, 'BlockEntities', ('list', 10, converted)))
                print(f'BlockEntities: {len(converted)} total, {items_count} with items')
        else:
            v2_entries.append((ct, cn, cv))

    writer = NBTWriter()
    writer.write_ubyte(10)
    writer.write_string('Schematic')
    for ct, cn, cv in v2_entries:
        writer.write_ubyte(ct)
        writer.write_string(cn)
        writer.write_payload(ct, cv)
    writer.write_ubyte(0)

    with gzip.open(output_path, 'wb') as f:
        f.write(writer.get_bytes())
    print(f'Saved: {output_path}')

    # Verify
    with gzip.open(output_path, 'rb') as f:
        vd = f.read()
    vr = NBTReader(vd)
    vrt = vr.read_ubyte()
    vrn = vr.read_string()
    print(f'Verify: root_name="{vrn}" (expect "Schematic")')
    vroot = vr.read_payload(vrt)
    _, vver = find_tag(vroot, 'Version')
    if vver:
        print(f'Verify: Version={vver[1]} (expect 2)')
    _, vpal = find_tag(vroot, 'Palette')
    print(f'Verify: Palette exists = {vpal is not None}')
    _, vbd = find_tag(vroot, 'BlockData')
    print(f'Verify: BlockData exists = {vbd is not None}')


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print(f'Usage: {sys.argv[0]} <input.schem> <output.schem>')
        sys.exit(1)
    convert_v3_to_v2(sys.argv[1], sys.argv[2])
