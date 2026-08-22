import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

/// Mirrors the desktop "Selbstverbesserung" tab: Siggi analyzes its own
/// knowledge gaps/tool errors daily and proposes fixes here for approval.
class ImprovementsScreen extends StatefulWidget {
  const ImprovementsScreen({super.key});
  @override
  State<ImprovementsScreen> createState() => _ImprovementsScreenState();
}

class _ImprovementsScreenState extends State<ImprovementsScreen> {
  List _suggestions = [];
  bool _loading = true;
  bool _running = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      _suggestions = (await Api.instance.get('/api/improvements') as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _runAnalysis() async {
    setState(() => _running = true);
    final res = await Api.instance.post('/api/improvements/run');
    _toast(res?['success'] == true ? '${res['new']} neu, ${res['auto_applied']} automatisch angewendet' : 'Fehler: ${res?['error'] ?? 'unbekannt'}');
    setState(() => _running = false);
    _load();
  }

  Future<void> _decide(int id, String action) async {
    await Api.instance.post('/api/improvements/$id/$action');
    _toast(action == 'approve' ? 'Übernommen' : 'Verworfen');
    _load();
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg), backgroundColor: context.colors.surface2));
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    final pending = _suggestions.where((s) => s['status'] == 'pending').toList();
    return Scaffold(
      appBar: AppBar(title: const Text('Selbstverbesserung')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  Align(
                    alignment: Alignment.centerRight,
                    child: ElevatedButton.icon(
                      onPressed: _running ? null : _runAnalysis,
                      icon: const Icon(Icons.psychology_outlined, size: 18),
                      label: Text(_running ? 'Analysiere...' : 'Analyse starten'),
                      style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10)),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text('Offene Vorschläge (${pending.length})', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  if (pending.isEmpty)
                    GlassCard(child: Text('Keine offenen Vorschläge.', style: TextStyle(color: c.dim)))
                  else
                    ...pending.map((s) => Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: GlassCard(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Row(children: [
                                  Icon(Icons.lightbulb_outline, color: warn, size: 18),
                                  const SizedBox(width: 8),
                                  Expanded(child: Text(s['title']?.toString() ?? '', style: TextStyle(color: c.text, fontWeight: FontWeight.w700, fontSize: 13))),
                                ]),
                                if ((s['detail'] ?? '').toString().isNotEmpty) ...[
                                  const SizedBox(height: 6),
                                  Text(s['detail'].toString(), style: TextStyle(color: c.dim, fontSize: 12)),
                                ],
                                const SizedBox(height: 12),
                                Row(children: [
                                  Expanded(
                                    child: OutlinedButton(
                                      onPressed: () => _decide(s['id'], 'dismiss'),
                                      style: OutlinedButton.styleFrom(foregroundColor: danger, side: const BorderSide(color: danger)),
                                      child: const Text('Verwerfen'),
                                    ),
                                  ),
                                  const SizedBox(width: 10),
                                  Expanded(
                                    child: ElevatedButton(
                                      onPressed: () => _decide(s['id'], 'approve'),
                                      child: const Text('Übernehmen'),
                                    ),
                                  ),
                                ]),
                              ],
                            ),
                          ),
                        )),
                ],
              ),
      ),
    );
  }
}
