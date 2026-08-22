import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class LinkedinScreen extends StatefulWidget {
  const LinkedinScreen({super.key});
  @override
  State<LinkedinScreen> createState() => _LinkedinScreenState();
}

class _LinkedinScreenState extends State<LinkedinScreen> {
  List _pending = [];
  bool _loading = true;
  bool _generating = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      _pending = (await Api.instance.get('/api/linkedin/pipeline/drafts?status=pending') as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _generate() async {
    setState(() => _generating = true);
    final res = await Api.instance.post('/api/linkedin/pipeline/generate');
    _toast(res?['success'] == true ? 'Entwurf erzeugt' : 'Fehler: ${res?['error'] ?? 'unbekannt'}');
    setState(() => _generating = false);
    _load();
  }

  Future<void> _decide(int id, String action) async {
    await Api.instance.post('/api/linkedin/pipeline/drafts/$id/$action');
    _toast(action == 'approve' ? 'Freigegeben' : 'Abgelehnt');
    _load();
  }

  void _toast(String msg) {
    if (!mounted) return;
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg), backgroundColor: context.colors.surface2));
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('LinkedIn')),
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
                      onPressed: _generating ? null : _generate,
                      icon: const Icon(Icons.auto_fix_high, size: 18),
                      label: Text(_generating ? 'Erzeuge...' : 'Neuer Entwurf'),
                      style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10)),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text('Zur Freigabe (${_pending.length})', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  if (_pending.isEmpty)
                    GlassCard(child: Text('Keine offenen Entwürfe.', style: TextStyle(color: c.dim)))
                  else
                    ..._pending.map((d) => Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: GlassCard(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                if ((d['topic'] ?? '').toString().isNotEmpty)
                                  Padding(
                                    padding: const EdgeInsets.only(bottom: 8),
                                    child: Text(d['topic'].toString(), style: TextStyle(color: accent2, fontSize: 12, fontWeight: FontWeight.w700)),
                                  ),
                                Text(d['text']?.toString() ?? '', style: TextStyle(color: c.text, fontSize: 13)),
                                const SizedBox(height: 14),
                                Row(children: [
                                  Expanded(
                                    child: OutlinedButton(
                                      onPressed: () => _decide(d['id'], 'reject'),
                                      style: OutlinedButton.styleFrom(foregroundColor: danger, side: const BorderSide(color: danger)),
                                      child: const Text('Ablehnen'),
                                    ),
                                  ),
                                  const SizedBox(width: 10),
                                  Expanded(
                                    child: ElevatedButton(
                                      onPressed: () => _decide(d['id'], 'approve'),
                                      child: const Text('Freigeben'),
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
