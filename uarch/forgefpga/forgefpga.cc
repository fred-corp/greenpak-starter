/*
 * forgefpga -- a nextpnr-generic Viaduct micro-architecture for Renesas
 * ForgeFPGA (SLG470xx/471xx/475xx/479xx) and GreenPAK (SLG46xxx) devices.
 *
 * The device fabric (BELs, switch matrix wires, PIPs) is loaded at runtime
 * from a JSON chipdb produced by the reverse-engineering tooling in this
 * repository (chipdb/gen_chipdb.py and the Ghidra decompilation passes).
 *
 * Usage:
 *   nextpnr-generic --uarch forgefpga --vopt chipdb=slg47125.json \
 *       --json design.json --freq 10 --package TSSOP-20
 *
 * Chipdb JSON schema (see chipdb/gen_chipdb.py for the generator):
 * {
 *   "device":  "SLG47125V",
 *   "wires":   [ {"name": "M0", "type": "MATRIX", "x": 0, "y": 0}, ... ],
 *   "bels":    [ {"name": "LUT_0", "type": "LUT4", "x": 0, "y": 0, "z": 0,
 *                 "ports": {"I0": {"dir": "in",  "wire": "M0"},
 *                           "F":  {"dir": "out", "wire": "M1"} } }, ...],
 *   "pips":    [ {"name": "M0.M1", "src": "M0", "dst": "M1",
 *                 "x": 0, "y": 0, "delay": 0.05}, ...],
 *   "io":      [ {"name": "IO_0", "x": 0, "y": 0, "z": 0,
 *                 "ports": {"PAD": {"dir":"inout","wire":"IO0"}}} ]
 * }
 */

#include "log.h"
#include "nextpnr.h"
#include "util.h"
#include "viaduct_api.h"
#include "viaduct_helpers.h"

#define GEN_INIT_CONSTIDS
#define VIADUCT_CONSTIDS "viaduct/forgefpga/constids.inc"
#include "viaduct_constids.h"

#include <fstream>
#include <map>
#include <memory>
#include <sstream>
#include <string>
#include <vector>

NEXTPNR_NAMESPACE_BEGIN

namespace {

// ---------------------------------------------------------- tiny JSON parser --
// Self-contained JSON subset parser (objects, arrays, strings, numbers,
// bools, null). Returns nullptr on malformed input with an error message.

struct JValue;
using JPtr = std::shared_ptr<JValue>;

struct JValue
{
    enum Type
    {
        NULL_T,
        BOOL_T,
        NUM_T,
        STR_T,
        ARR_T,
        OBJ_T
    } type = NULL_T;
    bool b = false;
    double num = 0;
    std::string str;
    std::vector<JPtr> arr;
    std::map<std::string, JPtr> obj;

    JPtr get(const std::string &key) const
    {
        auto it = obj.find(key);
        return it == obj.end() ? nullptr : it->second;
    }
    double num_or(const std::string &key, double dflt) const
    {
        JPtr v = get(key);
        return (v && v->type == NUM_T) ? v->num : dflt;
    }
    std::string str_or(const std::string &key, const std::string &dflt) const
    {
        JPtr v = get(key);
        return (v && v->type == STR_T) ? v->str : dflt;
    }
};

struct JParser
{
    const std::string &src;
    size_t pos = 0;

    explicit JParser(const std::string &s) : src(s) {}

    [[noreturn]] void fail(const char *msg)
    {
        log_error("forgefpga: chipdb JSON parse error at offset %zu: %s\n", pos, msg);
    }
    void ws()
    {
        while (pos < src.size() && (src[pos] == ' ' || src[pos] == '\t' || src[pos] == '\n' || src[pos] == '\r'))
            pos++;
    }
    bool lit(const char *s)
    {
        size_t n = strlen(s);
        if (src.compare(pos, n, s) == 0) {
            pos += n;
            return true;
        }
        return false;
    }
    JPtr value()
    {
        ws();
        if (pos >= src.size())
            fail("unexpected end of input");
        char c = src[pos];
        if (c == '{')
            return obj();
        if (c == '[')
            return array();
        if (c == '"')
            return string();
        if (lit("true") || lit("false")) {
            auto v = std::make_shared<JValue>();
            v->type = JValue::BOOL_T;
            v->b = (c == 't');
            return v;
        }
        if (lit("null"))
            return std::make_shared<JValue>();
        return number();
    }
    JPtr number()
    {
        size_t start = pos;
        if (pos < src.size() && (src[pos] == '-' || src[pos] == '+'))
            pos++;
        while (pos < src.size() && (isdigit(src[pos]) || src[pos] == '.' || src[pos] == 'e' || src[pos] == 'E' ||
                                    src[pos] == '-' || src[pos] == '+'))
            pos++;
        auto v = std::make_shared<JValue>();
        v->type = JValue::NUM_T;
        v->num = std::stod(src.substr(start, pos - start));
        return v;
    }
    JPtr string()
    {
        if (src[pos] != '"')
            fail("expected string");
        pos++;
        std::string out;
        while (pos < src.size() && src[pos] != '"') {
            if (src[pos] == '\\') {
                pos++;
                if (pos >= src.size())
                    fail("bad escape");
                switch (src[pos]) {
                case '"':
                    out += '"';
                    break;
                case '\\':
                    out += '\\';
                    break;
                case '/':
                    out += '/';
                    break;
                case 'n':
                    out += '\n';
                    break;
                case 't':
                    out += '\t';
                    break;
                case 'r':
                    out += '\r';
                    break;
                case 'u':
                    if (pos + 4 >= src.size())
                        fail("bad \\u escape");
                    out += (char)std::stoi(src.substr(pos + 1, 4), nullptr, 16);
                    pos += 4;
                    break;
                default:
                    fail("bad escape");
                }
            } else {
                out += src[pos];
            }
            pos++;
        }
        if (pos >= src.size())
            fail("unterminated string");
        pos++;
        auto v = std::make_shared<JValue>();
        v->type = JValue::STR_T;
        v->str = out;
        return v;
    }
    JPtr array()
    {
        auto v = std::make_shared<JValue>();
        v->type = JValue::ARR_T;
        pos++; // [
        ws();
        if (pos < src.size() && src[pos] == ']') {
            pos++;
            return v;
        }
        while (true) {
            v->arr.push_back(value());
            ws();
            if (pos >= src.size())
                fail("unterminated array");
            if (src[pos] == ',') {
                pos++;
                continue;
            }
            if (src[pos] == ']') {
                pos++;
                return v;
            }
            fail("expected ',' or ']'");
        }
    }
    JPtr obj()
    {
        auto v = std::make_shared<JValue>();
        v->type = JValue::OBJ_T;
        pos++; // {
        ws();
        if (pos < src.size() && src[pos] == '}') {
            pos++;
            return v;
        }
        while (true) {
            ws();
            JPtr key = string();
            ws();
            if (pos >= src.size() || src[pos] != ':')
                fail("expected ':'");
            pos++;
            v->obj[key->str] = value();
            ws();
            if (pos >= src.size())
                fail("unterminated object");
            if (src[pos] == ',') {
                pos++;
                continue;
            }
            if (src[pos] == '}') {
                pos++;
                return v;
            }
            fail("expected ',' or '}'");
        }
    }
};

// --------------------------------------------------------------- the uarch ----

struct ForgeFpgaImpl : ViaductAPI
{
    void init(Context *ctx) override
    {
        init_uarch_constids(ctx);
        ViaductAPI::init(ctx);
        h.init(ctx);

        auto it = uarch_args.find("chipdb");
        if (it == uarch_args.end() || it->second.empty())
            log_error("forgefpga: missing chipdb; use --vopt chipdb=<file>.json\n");
        std::ifstream f(it->second);
        if (!f)
            log_error("forgefpga: failed to open chipdb '%s'\n", it->second.c_str());
        std::stringstream ss;
        ss << f.rdbuf();
        std::string text = ss.str();

        JParser parser(text);
        JPtr root = parser.value();
        if (!root || root->type != JValue::OBJ_T)
            log_error("forgefpga: chipdb root must be an object\n");

        log_info("forgefpga: loading device '%s'\n", root->str_or("device", "<unknown>").c_str());
        init_wires(root);
        init_pips(root);
        init_bels(root, root->get("bels"));
        init_bels(root, root->get("io"));
    }

    // Wires: named routing resources (GreenPAK/ForgeFPGA switch matrix nets).
    void init_wires(const JPtr &root)
    {
        JPtr wires = root->get("wires");
        if (!wires)
            return;
        for (const auto &w : wires->arr) {
            int x = (int)w->num_or("x", 0), y = (int)w->num_or("y", 0);
            std::string name = w->str_or("name", "");
            auto idname = IdStringList(ctx->id(name));
            ctx->addWire(idname, ctx->id(w->str_or("type", "WIRE")), x, y);
            wire_by_name[name] = ctx->getWireByName(idname);
        }
        log_info("forgefpga: %zu wires\n", wires->arr.size());
    }

    WireId wire(const JPtr &j, const char *key) const
    {
        auto it = wire_by_name.find(j->str_or(key, ""));
        if (it == wire_by_name.end())
            return WireId();
        return it->second;
    }

    void init_pips(const JPtr &root)
    {
        JPtr pips = root->get("pips");
        if (!pips)
            return;
        for (const auto &p : pips->arr) {
            WireId src = wire(p, "src");
            WireId dst = wire(p, "dst");
            if (src == WireId() || dst == WireId())
                log_error("forgefpga: pip '%s' references unknown wire\n", p->str_or("name", "").c_str());
            std::string name = p->str_or("name", p->str_or("src", "") + "." + p->str_or("dst", ""));
            Loc loc((int)p->num_or("x", 0), (int)p->num_or("y", 0), 0);
            ctx->addPip(IdStringList(ctx->id(name)), ctx->id("PIP"), src, dst,
                         (delay_t)p->num_or("delay", 0.05), loc);
        }
        log_info("forgefpga: %zu pips\n", pips->arr.size());
    }

    // BELs: macrocell instances (LUTs, DFFs, counters, ...) and IO pads.
    void init_bels(const JPtr &root, const JPtr &bels)
    {
        if (!bels)
            return;
        for (const auto &b : bels->arr) {
            std::string name = b->str_or("name", "");
            IdString type = ctx->id(b->str_or("type", "BEL"));
            if (type == id_LUT4)
                has_lut4 = true;
            Loc loc((int)b->num_or("x", 0), (int)b->num_or("y", 0), (int)b->num_or("z", 0));
            BelId bel = ctx->addBel(IdStringList(ctx->id(name)), type, loc,
                                     b->num_or("global", false), b->num_or("locked", false));
            JPtr ports = b->get("ports");
            if (ports) {
                for (const auto &kv : ports->obj) {
                    IdString port = ctx->id(kv.first);
                    std::string dir = kv.second->str_or("dir", "in");
                    auto wit = wire_by_name.find(kv.second->str_or("wire", ""));
                    if (wit == wire_by_name.end())
                        log_error("forgefpga: bel '%s' port '%s' references unknown wire\n",
                                  name.c_str(), kv.first.c_str());
                    WireId w = wit->second;
                    if (dir == "in")
                        ctx->addBelInput(bel, port, w);
                    else if (dir == "out")
                        ctx->addBelOutput(bel, port, w);
                    else
                        ctx->addBelInout(bel, port, w);
                }
            }
        }
        log_info("forgefpga: %zu bels\n", bels->arr.size());
    }

    // Pack: strip automatic IO buffers; fold constants onto LUT4 when the
    // device has LUT4 macrocells (ForgeFPGA); GreenPAK devices use their
    // own GP_VDD/GP_VSS BELs.
    void pack() override
    {
        const pool<CellTypePort> top_ports{
                CellTypePort(id_INBUF, id_PAD),
                CellTypePort(id_OUTBUF, id_PAD),
                // yosys greenpak4 iopadmap buffers already in the netlist
                CellTypePort(id_GP_IBUF, ctx->id("IN")),
                CellTypePort(id_GP_OBUF, ctx->id("OUT")),
                CellTypePort(id_GP_IOBUF, ctx->id("IO")),
        };
        h.remove_nextpnr_iobs(top_ports);
        auto pit = uarch_args.find("pcf");
        if (pit != uarch_args.end() && !pit->second.empty())
            apply_pcf(pit->second);
        if (has_lut4) {
            const dict<IdString, Property> vcc_params = {{id_INIT, Property(0xFFFF, 16)}};
            const dict<IdString, Property> gnd_params = {{id_INIT, Property(0x0000, 16)}};
            h.replace_constants(CellTypePort(id_LUT4, id_F), CellTypePort(id_LUT4, id_F), vcc_params,
                                gnd_params);
        }
    }

    delay_t estimateDelay(WireId src, WireId dst) const override
    {
        // The switch matrix is a flat crossbar: constant hop cost.
        return 0.05;
    }

    delay_t predictDelay(BelId src_bel, IdString src_pin, BelId dst_bel, IdString dst_pin) const override
    {
        return estimateDelay(WireId(), WireId());
    }

    BoundingBox getRouteBoundingBox(WireId src, WireId dst) const override
    {
        BoundingBox bb;
        bb.x0 = bb.y0 = 0;
        bb.x1 = bb.y1 = std::numeric_limits<int>::max();
        return bb;
    }

    // Bel buckets: all logic LUT macrocells share one bucket (a smaller
    // LUT function is valid in a wider LUT macrocell - the unused input
    // stays unconnected = GND), IO pads grouped together.
    IdString getBelBucketForBel(BelId bel) const override
    {
        IdString type = ctx->getBelType(bel);
        if (type.in(id_IOB, id_IOB_OUT))
            return id_IOB;
        if (type.in(id_GP_2LUT, id_GP_3LUT, id_GP_4LUT))
            return id_GP_LUT;
        return type;
    }

    IdString getBelBucketForCellType(IdString cell_type) const override
    {
        if (cell_type.in(id_INBUF, id_OUTBUF, id_IOBUF, id_GP_IBUF, id_GP_OBUF, id_GP_IOBUF))
            return id_IOB;
        if (cell_type.in(id_GP_2LUT, id_GP_3LUT, id_GP_4LUT))
            return id_GP_LUT;
        // GP_DFF*/GP_DLATCH*/GP_PGEN live in the LUT macrocells
        {
            std::string t = cell_type.str(ctx);
            if (t.rfind("GP_DFF", 0) == 0 || t.rfind("GP_DLATCH", 0) == 0 ||
                cell_type == id_GP_PGEN)
                return id_GP_LUT;
        }
        return cell_type;
    }

    bool isValidBelForCellType(IdString cell_type, BelId bel) const override
    {
        IdString bel_type = ctx->getBelType(bel);
        if (bel_type == id_IOB)
            return cell_type.in(id_INBUF, id_OUTBUF, id_IOBUF, id_GP_IBUF, id_GP_OBUF, id_GP_IOBUF);
        if (bel_type == id_IOB_OUT)
            // output-only pad (e.g. GreenPAK GPO pins): no input buffer
            return cell_type.in(id_OUTBUF, id_GP_OBUF);
        if (cell_type == id_GP_2LUT)
            return bel_type.in(id_GP_2LUT, id_GP_3LUT, id_GP_4LUT);
        if (cell_type == id_GP_3LUT)
            return bel_type.in(id_GP_3LUT, id_GP_4LUT);
        // DFF/latch cells: valid on LUT macrocells that have the DFF
        // mode ports (the chipdb only adds them to DFF-capable slots),
        // and only when the cell's control input exists on the BEL
        // (e.g. GP_DFFR needs nRST, which LUT2-slot DFFs lack)
        {
            std::string t = cell_type.str(ctx);
            if (t.rfind("GP_DFF", 0) == 0 || t.rfind("GP_DLATCH", 0) == 0) {
                pool<IdString> pins;
                for (auto pin : ctx->getBelPins(bel))
                    pins.insert(pin);
                if (!pins.count(id_CLK) || !pins.count(id_D) || !pins.count(id_Q))
                    return false;
                if (cell_type == id_GP_DFFR && !pins.count(id_nRST))
                    return false;
                if (cell_type == id_GP_DFFS && !pins.count(id_nSET))
                    return false;
                return bel_type.in(id_GP_2LUT, id_GP_3LUT, id_GP_4LUT);
            }
            // The pattern generator lives in the dedicated LUT2_3/PGEN
            // slot (registers 1164-1183, mode select 1235)
            if (cell_type == id_GP_PGEN)
                return ctx->getBelName(bel).str(ctx) == "LUT2_3";
        }
        return bel_type == cell_type;
    }

    // Dump a placed/routed report for the bitstream assembler
    // (--vopt rpt=<file>): per-cell BEL binding + parameters, and every
    // bound PIP with its net and wires.
    void postRoute() override
    {
        auto it = uarch_args.find("rpt");
        if (it == uarch_args.end() || it->second.empty())
            return;
        std::ofstream f(it->second);
        if (!f)
            log_error("forgefpga: cannot open report file '%s'\n", it->second.c_str());
        f << "{\n\"cells\": {\n";
        bool first = true;
        for (auto &cell : ctx->cells) {
            CellInfo *ci = cell.second.get();
            if (!first)
                f << ",\n";
            first = false;
            f << "  \"" << ci->name.str(ctx) << "\": {\"type\": \"" << ci->type.str(ctx)
              << "\", \"bel\": \""
              << (ci->bel == BelId() ? std::string() : ctx->getBelName(ci->bel).str(ctx)) << "\"";
            f << ", \"params\": {";
            bool pfirst = true;
            for (const auto &kv : ci->params) {
                if (!pfirst)
                    f << ", ";
                pfirst = false;
                f << "\"" << kv.first.str(ctx) << "\": \"" << kv.second.to_string() << "\"";
            }
            f << "}}";
        }
        f << "\n},\n\"pips\": {\n";
        first = true;
        for (auto pip : ctx->getPips()) {
            NetInfo *net = ctx->getBoundPipNet(pip);
            if (!net)
                continue;
            if (!first)
                f << ",\n";
            first = false;
            f << "  \"" << ctx->getPipName(pip).str(ctx) << "\": {\"net\": \""
              << net->name.str(ctx) << "\", \"src\": \""
              << ctx->getWireName(ctx->getPipSrcWire(pip)).str(ctx) << "\", \"dst\": \""
              << ctx->getWireName(ctx->getPipDstWire(pip)).str(ctx) << "\"}";
        }
        f << "\n}\n}\n";
        log_info("forgefpga: routed report written to %s\n", it->second.c_str());
    }

    // Pin constraints: PCF file (--vopt pcf=<file>).
    // Supported line syntaxes:
    //   set_io <port> <bel>                          (nextpnr style)
    //   set_property PACKAGE_PIN <bel> [get_ports <port>]   (XDC style)
    // <port> is a top-level port name, optionally a vector bit like a[0].
    // <bel> is an IO BEL name from the chipdb (e.g. PIN16 on GreenPAK).
    void apply_pcf(const std::string &filename)
    {
        std::ifstream f(filename);
        if (!f)
            log_error("forgefpga: cannot open pcf '%s'\n", filename.c_str());
        std::string line;
        int lineno = 0;
        while (std::getline(f, line)) {
            lineno++;
            auto hash = line.find('#');
            if (hash != std::string::npos)
                line = line.substr(0, hash);
            std::istringstream ss(line);
            std::vector<std::string> t;
            std::string tok;
            while (ss >> tok)
                t.push_back(tok);
            if (t.empty())
                continue;
            std::string port, pin;
            if (t[0] == "set_io" && t.size() >= 3) {
                port = t[1];
                pin = t[2];
            } else if (t[0] == "set_property" && t.size() >= 5 && t[1] == "PACKAGE_PIN") {
                pin = t[2];
                port = t[4];
                // strip XDC decorations: {a[0]} / a[0]} etc.
                auto not_id = [](char c) { return c == ']' || c == '}' || c == '{'; };
                while (!port.empty() && not_id(port.back()))
                    port.pop_back();
                while (!port.empty() && not_id(port.front()))
                    port = port.substr(1);
            } else {
                continue;
            }
            constrain_port(port, pin, lineno);
        }
    }

    CellInfo *find_pad_peer(NetInfo *net) const
    {
        if (!net)
            return nullptr;
        if (net->driver.cell && net->driver.port == id_PAD)
            return net->driver.cell;
        CellInfo *found = nullptr;
        for (auto &usr : net->users) {
            if (usr.port != id_PAD)
                continue;
            if (found && usr.cell != found)
                log_error("Multiple IO cells on PAD net '%s'\n", ctx->nameOf(net));
            found = usr.cell;
        }
        return found;
    }

    void constrain_port(const std::string &port, const std::string &pin, int lineno)
    {
        // Find the top-level port net (scalar name or vector bit a[0];
        // nextpnr names top port nets after the port bits). The pad cell
        // is the IO buffer driving (or using) that net - yosys iopadmap
        // created it, so no $nextpnr_* helpers exist.
        IdString net_name = ctx->id(port);
        auto net_it = ctx->nets.find(net_name);
        if (net_it == ctx->nets.end() && port.size() >= 3 && port.substr(port.size() - 3) == "[0]")
            net_it = ctx->nets.find(ctx->id(port.substr(0, port.size() - 3)));
        if (net_it == ctx->nets.end()) {
            log_warning("pcf line %d: port '%s' not found - ignored\n", lineno, port.c_str());
            return;
        }
        NetInfo *net = net_it->second.get();

        CellInfo *io_cell = nullptr;
        auto check = [&](CellInfo *c) {
            if (!c)
                return;
            if (!c->type.in(id_GP_IBUF, id_GP_OBUF, id_GP_IOBUF, id_INBUF, id_OUTBUF, id_IOBUF))
                return;
            if (io_cell && io_cell != c)
                log_error("pcf line %d: multiple IO cells on port '%s'\n", lineno, port.c_str());
            io_cell = c;
        };
        check(net->driver.cell);
        for (auto &usr : net->users)
            check(usr.cell);
        if (!io_cell)
            log_error("pcf line %d: no IO cell on port '%s' (was iopadmap run?)\n",
                      lineno, port.c_str());

        BelId bel = ctx->getBelByNameStr(pin);
        if (bel == BelId())
            log_error("pcf line %d: no BEL named '%s' in chipdb\n", lineno, pin.c_str());
        if (ctx->getBelType(bel) != id_IOB)
            log_error("pcf line %d: BEL '%s' is not an IO BEL\n", lineno, pin.c_str());
        if (io_cell->attrs.count(id_BEL))
            log_error("pcf line %d: duplicate constraint for '%s'\n", lineno, port.c_str());
        io_cell->attrs[id_BEL] = ctx->getBelName(bel).str(ctx);
        log_info("constrained '%s' to BEL '%s'\n", port.c_str(), pin.c_str());
    }

  private:
    ViaductHelpers h;
    std::map<std::string, WireId> wire_by_name;
    bool has_lut4 = false;

  public:
    dict<std::string, std::string> uarch_args;
};

struct ForgeFpgaArch : ViaductArch
{
    ForgeFpgaArch() : ViaductArch("forgefpga") {}
    std::unique_ptr<ViaductAPI> create(const dict<std::string, std::string> &args) override
    {
        auto impl = std::make_unique<ForgeFpgaImpl>();
        impl->uarch_args = args;
        return impl;
    }
} forgeFpgaArch;

// The same data-driven implementation serves the GreenPAK parts
// (GP_2LUT/GP_3LUT/GP_4LUT cell families, per-device chipdbs); register
// it under the greenpak name so both cell conventions can be used with
// their natural uarch name.
struct GreenpakArch : ViaductArch
{
    GreenpakArch() : ViaductArch("greenpak") {}
    std::unique_ptr<ViaductAPI> create(const dict<std::string, std::string> &args) override
    {
        auto impl = std::make_unique<ForgeFpgaImpl>();
        impl->uarch_args = args;
        return impl;
    }
} greenpakArch;

} // namespace

NEXTPNR_NAMESPACE_END
