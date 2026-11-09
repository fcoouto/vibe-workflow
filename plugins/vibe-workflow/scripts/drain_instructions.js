async function drainInstructions({root, paths, exec, emit}) {
    const quote = s => "'" + s.replace(/'/g, "'\\''") + "'";
    const reader = ["python3", "-I", "-B", root + "/scripts/read_instructions.py"].map(quote).join(" ");
    const selection = " --auto -- " + paths.map(quote).join(" ");
    const read = (page, revision, batch) => exec({
        cmd: reader + " --json" + (batch ? " --batch" : "") + " --page " + page +
            (revision ? " --revision " + revision : "") + selection,
        max_output_tokens: 10000, yield_time_ms: 10000
    });
    const decode = r => {
        if (r.session_id || r.exit_code !== 0) throw new Error("Instruction delivery incomplete: reader did not finish successfully");
        let p; try { p = JSON.parse(r.output); } catch { return null; }
        if (!p || p.version !== 1 || p.kind !== "instructions" || typeof p.text !== "string" ||
            !Number.isSafeInteger(p.characters) || p.characters < 1 || [...p.text].length !== p.characters) return null;
        if (![p.page,p.last_page,p.pages].every(Number.isSafeInteger) || p.page < 1 ||
            p.page > p.last_page || p.last_page > p.pages || !/^[a-f0-9]{64}$/.test(p.revision) ||
            p.mode !== "auto" || p.external !== false || typeof p.batch !== "boolean") throw new Error("Invalid instruction metadata");
        return p;
    };
    let packet = decode(await read(1, null, true)), page = 1, revision = null, pages = null, batch = true, delivered = 0;
    while (true) {
        if (packet === null) {
            if (!batch) throw new Error("Instruction delivery incomplete: small-page output was omitted");
            // Keep the missing range and revision; do not replay delivered pages.
            batch = false;
            packet = decode(await read(page, revision, false));
            if (packet === null) throw new Error("Instruction delivery incomplete: small-page output was omitted");
        }
        if (packet.page !== page || packet.batch !== batch ||
            (!batch && packet.last_page !== page) ||
            (revision !== null && packet.revision !== revision) ||
            (pages !== null && packet.pages !== pages)) {
            throw new Error("Instruction delivery incomplete: selection revision or page sequence changed");
        }
        revision = packet.revision;
        pages = packet.pages;
        await emit(packet.text);
        delivered++;
        if (packet.last_page === pages) return {complete: true, pages, batches: delivered, revision};
        page = packet.last_page + 1;
        packet = decode(await read(page, revision, batch));
    }
}
