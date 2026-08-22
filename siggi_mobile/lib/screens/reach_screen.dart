import 'package:flutter/material.dart';
import '../api.dart';
import '../theme.dart';

const _metricLabel = {
  'reach': 'Reichweite', 'saved': 'Gespeichert', 'shares': 'Geteilt',
  'total_interactions': 'Interaktionen', 'plays': 'Aufrufe', 'video_views': 'Video-Views',
};

class ReachScreen extends StatefulWidget {
  const ReachScreen({super.key});
  @override
  State<ReachScreen> createState() => _ReachScreenState();
}

class _ReachScreenState extends State<ReachScreen> {
  List _posts = [];
  bool _loading = true;
  bool _missingPermission = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    setState(() => _loading = true);
    try {
      final data = await Api.instance.get('/api/instagram/insights');
      _posts = (data?['posts'] as List?) ?? [];
      _missingPermission = _posts.any((p) => p['insights_permission_missing'] == true);
    } catch (_) {}
    if (mounted) setState(() => _loading = false);
  }

  @override
  Widget build(BuildContext context) {
    final c = context.colors;
    return Scaffold(
      appBar: AppBar(title: const Text('Reichweite')),
      body: RefreshIndicator(
        onRefresh: _load,
        child: _loading
            ? const Center(child: CircularProgressIndicator())
            : ListView(
                padding: const EdgeInsets.all(16),
                children: [
                  if (_missingPermission)
                    Padding(
                      padding: const EdgeInsets.only(bottom: 16),
                      child: GlassCard(
                        child: Text(
                          '⚠️ Likes/Kommentare werden angezeigt, aber echte Reichweiten-Zahlen fehlen - '
                          'dafür braucht der Instagram-Token die Berechtigung "instagram_manage_insights" '
                          '(einmalig über den Graph API Explorer nachtragen).',
                          style: TextStyle(color: c.dim, fontSize: 12),
                        ),
                      ),
                    ),
                  if (_posts.isEmpty)
                    Text('Noch keine Posts mit gespeicherter Media-ID.', style: TextStyle(color: c.dim))
                  else
                    ..._posts.map((p) => Padding(
                          padding: const EdgeInsets.only(bottom: 10),
                          child: GlassCard(
                            child: Column(
                              crossAxisAlignment: CrossAxisAlignment.start,
                              children: [
                                Text(p['filename']?.toString() ?? '', style: TextStyle(color: c.text, fontWeight: FontWeight.w700, fontSize: 13)),
                                const SizedBox(height: 10),
                                Wrap(spacing: 16, runSpacing: 8, children: [
                                  _stat('❤️', p['like_count'], c),
                                  _stat('💬', p['comments_count'], c),
                                  ...((p['insights'] as Map?) ?? {}).entries.map((e) => _stat(_metricLabel[e.key] ?? e.key, e.value, c)),
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

  Widget _stat(String label, dynamic value, dynamic c) {
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text('${value ?? '–'}', style: const TextStyle(color: good, fontWeight: FontWeight.w800, fontSize: 16)),
        Text(label, style: TextStyle(color: c.dim, fontSize: 10)),
      ],
    );
  }
}
