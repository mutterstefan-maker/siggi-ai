import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

class FlyerScreen extends StatefulWidget {
  const FlyerScreen({super.key});
  @override
  State<FlyerScreen> createState() => _FlyerScreenState();
}

class _FlyerScreenState extends State<FlyerScreen> {
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
      _pending = (await Api.instance.get('/api/instagram/flyer-pipeline/pending') as List?) ?? [];
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  Future<void> _generate() async {
    setState(() => _generating = true);
    final res = await Api.instance.post('/api/instagram/flyer-pipeline/generate');
    _toast(res?['success'] == true ? 'Bild erzeugt' : 'Fehler: ${res?['error'] ?? 'unbekannt'}');
    setState(() => _generating = false);
    _load();
  }

  Future<void> _decide(int id, String action) async {
    await Api.instance.post('/api/instagram/flyer-pipeline/$id/$action', {});
    _toast(action == 'approve' ? 'Freigegeben → Warteschlange' : 'Abgelehnt');
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
      appBar: AppBar(title: const Text('Bilder')),
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
                      label: Text(_generating ? 'Erzeuge...' : 'Neues Bild'),
                      style: ElevatedButton.styleFrom(padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10)),
                    ),
                  ),
                  const SizedBox(height: 16),
                  Text('Zur Freigabe (${_pending.length})', style: TextStyle(color: c.text, fontSize: 16, fontWeight: FontWeight.w700)),
                  const SizedBox(height: 10),
                  if (_pending.isEmpty)
                    GlassCard(child: Text('Keine offenen Bilder.', style: TextStyle(color: c.dim)))
                  else
                    ..._pending.map((f) => Padding(
                          padding: const EdgeInsets.only(bottom: 12),
                          child: GlassCard(
                            padding: EdgeInsets.zero,
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.stretch,
                              children: [
                                ClipRRect(
                                  borderRadius: const BorderRadius.vertical(top: Radius.circular(18)),
                                  child: Image.network(
                                    Api.instance.mediaUrl('/api/instagram/flyer-pipeline/image/${f['filename']}'),
                                    fit: BoxFit.cover, height: 220,
                                    errorBuilder: (_, _, _) => Container(height: 220, color: c.surface2, child: Icon(Icons.image_not_supported, color: c.dim)),
                                  ),
                                ),
                                Padding(
                                  padding: const EdgeInsets.all(14),
                                  child: Column(
                                    crossAxisAlignment: CrossAxisAlignment.start,
                                    children: [
                                      Text(f['topic']?.toString() ?? '', style: TextStyle(color: c.text, fontWeight: FontWeight.w700)),
                                      if ((f['headline'] ?? '').toString().isNotEmpty) ...[
                                        const SizedBox(height: 4),
                                        Text(f['headline'].toString(), style: TextStyle(color: c.dim, fontSize: 12)),
                                      ],
                                      const SizedBox(height: 12),
                                      Row(children: [
                                        Expanded(
                                          child: OutlinedButton(
                                            onPressed: () => _decide(f['id'], 'reject'),
                                            style: OutlinedButton.styleFrom(foregroundColor: danger, side: const BorderSide(color: danger)),
                                            child: const Text('Ablehnen'),
                                          ),
                                        ),
                                        const SizedBox(width: 10),
                                        Expanded(
                                          child: ElevatedButton(
                                            onPressed: () => _decide(f['id'], 'approve'),
                                            child: const Text('Freigeben'),
                                          ),
                                        ),
                                      ]),
                                    ],
                                  ),
                                ),
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
